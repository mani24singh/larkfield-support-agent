"""Every response must satisfy the supplied response schema exactly."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api import create_app
from src.config import Settings
from src.models import LedgerEntry, Outcome, TicketResponse, TicketType
from src.policy import ReasonCode

ROOT = Path(__file__).resolve().parent.parent
INBOX = json.loads(
    (ROOT / "tickets" / "support_inbox_export.json").read_text(encoding="utf-8-sig")
)
PUBLIC = json.loads(
    (ROOT / "inbox" / "public_tickets.json").read_text(encoding="utf-8-sig")
)

VALID_RESULTS = {"PASS", "FAIL", "NOT_APPLICABLE", "UNABLE_TO_VERIFY"}
VALID_APPROVERS = {"SUPPORT_LEAD", "TRUST_SAFETY", "CARRIER_CLAIMS", None}


@pytest.fixture(scope="module")
def client(tmp_path_factory) -> TestClient:
    settings = Settings(
        runs_dir=tmp_path_factory.mktemp("schema-runs"), cadra_token=SecretStr("")
    )
    return TestClient(create_app(settings))


@pytest.fixture(scope="module")
def all_responses(client: TestClient) -> list[dict]:
    first = client.post("/tickets", json=INBOX)
    second = client.post("/tickets", json=PUBLIC)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    return first.json() + second.json()


def test_all_forty_responses_validate(all_responses: list[dict]) -> None:
    assert len(all_responses) == 40

    for raw in all_responses:
        response = TicketResponse.model_validate(raw)
        again = TicketResponse.model_validate(response.model_dump(mode="json"))
        assert again == response


def test_response_fields_respect_the_contract(all_responses: list[dict]) -> None:
    for raw in all_responses:
        response = TicketResponse.model_validate(raw)

        assert response.ticket_id
        assert response.run_id.startswith("run_")
        assert response.ticket_type in getattr(TicketType, "__args__", ())
        assert response.outcome in getattr(Outcome, "__args__", ())
        assert response.approver_role in VALID_APPROVERS
        assert isinstance(response.reason_codes, list)
        assert {code for code in response.reason_codes} <= {
            code.value for code in ReasonCode
        }
        assert isinstance(response.human_approval_required, bool)

        if response.outcome == "PEND":
            assert response.human_approval_required is True
            assert response.approver_role == "SUPPORT_LEAD"
        if response.outcome in {"REJECT", "INVALID_REQUEST", "UNABLE_TO_VERIFY"}:
            assert response.human_approval_required is False

        assert response.checks, f"{response.ticket_id} has no checks"
        for check in response.checks:
            assert check.check_id
            assert check.result in VALID_RESULTS
            assert check.policy_clause
            for row in check.evidence:
                assert row.source and row.ref and row.value


def test_resolved_identity_is_consistent(all_responses: list[dict]) -> None:
    for raw in all_responses:
        response = TicketResponse.model_validate(raw)

        if response.outcome == "INVALID_REQUEST":
            if "ORDER_NOT_FOUND" in response.reason_codes:
                assert response.resolved.order_id is None
            if "CUSTOMER_MISMATCH" in response.reason_codes:
                # We may have identified the order, but not as this sender's.
                assert response.resolved.order_id is not None
        if response.outcome in {"PEND", "PROCEED", "PROCEED_MODIFIED"}:
            assert response.resolved.order_id
            assert response.resolved.customer_id


def test_draft_reply_shape_and_trace_contract(all_responses: list[dict]) -> None:
    for raw in all_responses:
        response = TicketResponse.model_validate(raw)

        assert response.draft_reply.to
        assert response.draft_reply.subject.startswith("Re: ")
        assert not response.draft_reply.subject.lower().startswith("re: re:")
        assert response.draft_reply.body.strip()
        assert "Larkfield Home Customer Care" in response.draft_reply.body

        assert [doc.document for doc in response.trace.documents_used] == [
            "refund_policy_RP-3.2.md"
        ]
        assert response.trace.documents_used[0].version == "3.2"
        assert isinstance(response.trace.tools_called, list)
        assert isinstance(response.trace.model_calls, list)
        assert isinstance(response.trace.cost_usd, float)
        assert response.trace.cost_usd >= 0.0


def test_run_grouping_and_ids(all_responses: list[dict]) -> None:
    inbox_ids = [raw["ticket_id"] for raw in all_responses[:30]]
    public_ids = [raw["ticket_id"] for raw in all_responses[30:]]

    assert inbox_ids == [ticket["ticket_id"] for ticket in INBOX]
    assert public_ids == [ticket["ticket_id"] for ticket in PUBLIC]
    assert len({raw["run_id"] for raw in all_responses[:30]}) == 1
    assert len({raw["run_id"] for raw in all_responses[30:]}) == 1
    assert all_responses[0]["run_id"] != all_responses[30]["run_id"]


def test_ledger_entries_validate(client: TestClient, all_responses: list[dict]) -> None:
    for run_id in {raw["run_id"] for raw in all_responses}:
        response = client.get("/ledger", params={"run_id": run_id})
        assert response.status_code == 200
        for raw in response.json():
            entry = LedgerEntry.model_validate(raw)
            assert entry.run_id == run_id
            assert entry.action in {"REFUND", "REPLY", "ESCALATION"}
            assert entry.status in {"PROPOSED", "APPROVED", "REJECTED"}
            if entry.action == "REFUND":
                assert entry.amount_usd is not None
            if entry.action == "ESCALATION":
                assert entry.amount_usd is None
