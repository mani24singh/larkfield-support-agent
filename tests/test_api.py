"""HTTP acceptance tests: schema, batching, idempotency, approvals, ledger."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.config import Settings
from src.models import LedgerEntry, TicketResponse

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


INBOX = _load(ROOT / "tickets" / "support_inbox_export.json")
PUBLIC = _load(ROOT / "inbox" / "public_tickets.json")
BY_ID = {ticket["ticket_id"]: ticket for ticket in INBOX}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(runs_dir=tmp_path / "runs")
    return TestClient(create_app(settings))


def _post(client: TestClient, tickets: list[dict]) -> list[dict]:
    response = client.post("/tickets", json=tickets)
    assert response.status_code == 200, response.text
    return response.json()


def _run_tickets(client: TestClient, ticket_ids: list[str]) -> tuple[str, dict[str, dict]]:
    payload = [BY_ID[ticket_id] for ticket_id in ticket_ids]
    responses = _post(client, payload)
    assert len(responses) == len(ticket_ids)
    run_id = responses[0]["run_id"]
    assert {item["run_id"] for item in responses} == {run_id}
    return run_id, {item["ticket_id"]: item for item in responses}


def _entries(client: TestClient, run_id: str) -> list[dict]:
    response = client.get("/ledger", params={"run_id": run_id})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Schema and batching
# ---------------------------------------------------------------------------


def test_all_forty_tickets_validate(client: TestClient) -> None:
    inbox_responses = _post(client, INBOX)
    public_responses = _post(client, PUBLIC)

    assert len(inbox_responses) == 30
    assert len(public_responses) == 10

    inbox_run = {item["run_id"] for item in inbox_responses}
    public_run = {item["run_id"] for item in public_responses}
    assert len(inbox_run) == 1
    assert len(public_run) == 1
    assert inbox_run != public_run

    for item in inbox_responses + public_responses:
        validated = TicketResponse.model_validate(item)
        assert validated.ticket_id
        assert validated.draft_reply.body
        assert validated.trace.documents_used[0].document == "refund_policy_RP-3.2.md"

    assert [item["ticket_id"] for item in inbox_responses] == [
        ticket["ticket_id"] for ticket in INBOX
    ]
    assert [item["ticket_id"] for item in public_responses] == [
        ticket["ticket_id"] for ticket in PUBLIC
    ]


def test_single_ticket_object_returns_a_list(client: TestClient) -> None:
    response = client.post("/tickets", json=INBOX[0])

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["ticket_id"] == INBOX[0]["ticket_id"]


def test_responses_and_ledger_are_written_atomically(client: TestClient, tmp_path: Path) -> None:
    run_id, _ = _run_tickets(client, ["T002"])
    run_dir = tmp_path / "runs" / run_id

    responses = json.loads((run_dir / "responses.json").read_text(encoding="utf-8"))
    lines = (run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()

    assert [item["ticket_id"] for item in responses] == ["T002"]
    assert len(lines) == 1
    entry = LedgerEntry.model_validate_json(lines[0])
    assert entry.action == "REFUND"
    assert entry.status == "PROPOSED"


def test_run_and_health_endpoints(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200

    run_id, _ = _run_tickets(client, ["T002"])
    run = client.get(f"/runs/{run_id}")

    assert run.status_code == 200
    assert run.json()["run_id"] == run_id
    assert run.json()["responses"][0]["ticket_id"] == "T002"
    assert client.get("/runs/does-not-exist").status_code == 404


# ---------------------------------------------------------------------------
# Duplicate refunds
# ---------------------------------------------------------------------------


def test_duplicate_tickets_do_not_create_second_refund(client: TestClient) -> None:
    run_id, responses = _run_tickets(client, ["T002", "T027", "T005", "T028"])

    for duplicate_id in ("T027", "T028"):
        body = responses[duplicate_id]["draft_reply"]["body"].lower()
        assert "already being handled" in body
        assert "duplicate refund" in body

    refunds = [
        entry
        for entry in _entries(client, run_id)
        if entry["action"] == "REFUND"
    ]

    assert len(refunds) == 2, refunds
    by_amount = {entry["amount_usd"] for entry in refunds}
    assert by_amount == {129.0, 38.0}
    assert {entry["ticket_id"] for entry in refunds} == {"T002", "T005"}
    assert all(entry["status"] == "PROPOSED" for entry in refunds)


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def test_double_approval_is_idempotent(client: TestClient) -> None:
    run_id, responses = _run_tickets(client, ["T003"])
    assert responses["T003"]["outcome"] == "PEND"

    body = {"decision": "approve", "approver": "lead@larkfield.example"}
    first = client.post(
        "/approvals",
        params={"run_id": run_id, "ticket_id": "T003"},
        json=body,
    )
    second = client.post(
        "/approvals",
        params={"run_id": run_id, "ticket_id": "T003"},
        json=body,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "APPROVED"
    assert second.json()["status"] == "APPROVED"
    assert second.json()["recorded_at"] == first.json()["recorded_at"]

    refunds = [
        entry for entry in _entries(client, run_id) if entry["action"] == "REFUND"
    ]
    assert len(refunds) == 1
    assert refunds[0]["status"] == "APPROVED"


def test_approval_without_explicit_ticket_targets_latest_pending(client: TestClient) -> None:
    run_id, _ = _run_tickets(client, ["T002", "T003"])

    response = client.post(
        "/approvals",
        params={"run_id": run_id},
        json={"decision": "approve", "approver": "lead@larkfield.example"},
    )

    assert response.status_code == 200
    assert response.json()["ticket_id"] == "T003"


def test_rejection_sets_rejected(client: TestClient) -> None:
    run_id, _ = _run_tickets(client, ["T011"])

    response = client.post(
        "/approvals",
        params={"run_id": run_id, "ticket_id": "T011"},
        json={"decision": "reject", "approver": "claims@larkfield.example"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["action"] == "ESCALATION"
    assert response.json()["amount_usd"] is None


@pytest.mark.parametrize("ticket_id", ["T014", "T018", "T013"])
def test_approving_non_approvable_outcome_is_409(
    client: TestClient, ticket_id: str
) -> None:
    run_id, _ = _run_tickets(client, [ticket_id])

    response = client.post(
        "/approvals",
        params={"run_id": run_id, "ticket_id": ticket_id},
        json={"decision": "approve", "approver": "lead@larkfield.example"},
    )

    assert response.status_code == 409


def test_approving_a_reject_is_409(client: TestClient) -> None:
    run_id, responses = _run_tickets(client, ["T014"])
    assert responses["T014"]["outcome"] == "REJECT"

    response = client.post(
        "/approvals",
        params={"run_id": run_id, "ticket_id": "T014"},
        json={"decision": "approve", "approver": "lead@larkfield.example"},
    )

    assert response.status_code == 409
    assert "REJECT" in response.json()["detail"]


def test_contract_decision_endpoint_alias(client: TestClient) -> None:
    run_id, _ = _run_tickets(client, ["T003"])

    response = client.post(
        f"/runs/{run_id}/decision",
        params={"ticket_id": "T003"},
        json={"decision": "approve", "approver": "lead@larkfield.example"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# Ledger contents
# ---------------------------------------------------------------------------


def test_ledger_rows_follow_action_rules(client: TestClient) -> None:
    run_id, _ = _run_tickets(
        client, ["T001", "T008", "T011", "T013", "T014", "T019"]
    )
    entries = {entry["ticket_id"]: entry for entry in _entries(client, run_id)}

    assert entries["T001"]["action"] == "REFUND"
    assert entries["T001"]["amount_usd"] == 210.0
    assert entries["T008"]["action"] == "REPLY"
    assert entries["T008"]["amount_usd"] is None
    assert entries["T011"]["action"] == "ESCALATION"
    assert entries["T011"]["amount_usd"] is None
    assert entries["T019"]["action"] == "REFUND"
    assert entries["T019"]["amount_usd"] == 27.5

    for ticket_id in ("T013", "T014"):
        assert ticket_id not in entries

    assert all(entry["run_id"] == run_id for entry in entries.values())
    assert all(entry["status"] == "PROPOSED" for entry in entries.values())


def test_ledger_without_run_filter_lists_every_run(client: TestClient) -> None:
    first_run, _ = _run_tickets(client, ["T002"])
    second_run, _ = _run_tickets(client, ["T005"])

    entries = client.get("/ledger").json()

    assert {entry["run_id"] for entry in entries} == {first_run, second_run}
