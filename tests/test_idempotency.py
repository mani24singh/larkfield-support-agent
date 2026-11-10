"""Refund idempotency: duplicates, prior refunds and repeated approvals."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api import create_app
from src.config import Settings
from src.models import Ticket
from src.pipeline import evaluate
from src.policy import ReasonCode, adjudicate_ticket

ROOT = Path(__file__).resolve().parent.parent
INBOX = json.loads(
    (ROOT / "tickets" / "support_inbox_export.json").read_text(encoding="utf-8-sig")
)
BY_ID = {ticket["ticket_id"]: ticket for ticket in INBOX}
SETTINGS = Settings(cadra_token=SecretStr(""))


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(runs_dir=tmp_path / "runs", cadra_token=SecretStr(""))
    return TestClient(create_app(settings))


def _post(client: TestClient, ticket_ids: list[str]) -> tuple[str, list[dict]]:
    payload = [BY_ID[ticket_id] for ticket_id in ticket_ids]
    response = client.post("/tickets", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    return body[0]["run_id"], body


def _ledger(client: TestClient, run_id: str) -> list[dict]:
    response = client.get("/ledger", params={"run_id": run_id})
    assert response.status_code == 200
    return response.json()


def test_duplicate_ticket_creates_exactly_one_financial_action(client: TestClient) -> None:
    run_id, responses = _post(client, ["T002", "T027"])
    by_id = {item["ticket_id"]: item for item in responses}

    refunds = [entry for entry in _ledger(client, run_id) if entry["action"] == "REFUND"]

    assert len(refunds) == 1
    assert refunds[0]["ticket_id"] == "T002"
    assert refunds[0]["amount_usd"] == 129.0
    assert refunds[0]["status"] == "PROPOSED"
    assert "already being handled" in by_id["T027"]["draft_reply"]["body"].lower()
    assert "duplicate refund" in by_id["T027"]["draft_reply"]["body"].lower()
    assert by_id["T027"]["ticket_id"] != by_id["T002"]["ticket_id"]


def test_duplicate_pair_from_public_seed_is_also_suppressed(client: TestClient) -> None:
    run_id, responses = _post(client, ["T005", "T028"])

    refunds = [entry for entry in _ledger(client, run_id) if entry["action"] == "REFUND"]

    assert len(refunds) == 1
    assert refunds[0]["amount_usd"] == 38.0
    assert "already being handled" in responses[1]["draft_reply"]["body"].lower()


def test_already_refunded_line_has_zero_amount_and_is_rejected(dataset) -> None:
    ticket = Ticket.model_validate(BY_ID["T018"])

    decision = adjudicate_ticket(dataset, ticket)
    outcome = evaluate(ticket, dataset=dataset, settings=SETTINGS)

    assert decision.outcome == "REJECT"
    assert decision.amount == 0
    assert decision.human_approval_required is False
    assert decision.reason_codes == (ReasonCode.ALREADY_REFUNDED,)
    assert outcome.refund_keys == ()


def test_already_refunded_ticket_files_no_ledger_action(client: TestClient) -> None:
    run_id, responses = _post(client, ["T018"])

    assert responses[0]["outcome"] == "REJECT"
    assert _ledger(client, run_id) == []


def test_second_approval_is_a_no_op(client: TestClient) -> None:
    run_id, _ = _post(client, ["T003"])
    payload = {"decision": "approve", "approver": "lead@larkfield.example"}

    first = client.post(
        "/approvals", params={"run_id": run_id, "ticket_id": "T003"}, json=payload
    )
    second = client.post(
        "/approvals", params={"run_id": run_id, "ticket_id": "T003"}, json=payload
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "APPROVED"
    assert second.json() == first.json()

    refunds = [entry for entry in _ledger(client, run_id) if entry["action"] == "REFUND"]
    assert len(refunds) == 1
    assert refunds[0]["status"] == "APPROVED"


def test_rejecting_twice_is_also_a_no_op(client: TestClient) -> None:
    run_id, _ = _post(client, ["T011"])
    payload = {"decision": "reject", "approver": "claims@larkfield.example"}

    first = client.post(
        "/approvals", params={"run_id": run_id, "ticket_id": "T011"}, json=payload
    )
    second = client.post(
        "/approvals", params={"run_id": run_id, "ticket_id": "T011"}, json=payload
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "REJECTED"
    assert len(_ledger(client, run_id)) == 1
