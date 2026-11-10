"""The deterministic layer must return identical decisions for identical input."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.api import create_app
from src.config import Settings
from src.models import Ticket
from src.policy import adjudicate_ticket

ROOT = Path(__file__).resolve().parent.parent
INBOX = json.loads(
    (ROOT / "tickets" / "support_inbox_export.json").read_text(encoding="utf-8-sig")
)
PUBLIC = json.loads(
    (ROOT / "inbox" / "public_tickets.json").read_text(encoding="utf-8-sig")
)
TICKETS = INBOX + PUBLIC
SETTINGS = Settings(cadra_token=SecretStr(""))


def _fingerprint(dataset, raw: dict) -> dict:
    decision = adjudicate_ticket(dataset, Ticket.model_validate(raw))
    return {
        "outcome": decision.outcome,
        "reason_codes": tuple(code.value for code in decision.reason_codes),
        "amount": str(decision.amount),
        "approver_role": decision.approver_role,
        "human_approval_required": decision.human_approval_required,
        "checks": tuple(
            (
                check.check_id,
                check.result,
                check.policy_clause,
                tuple((row.source, row.ref, row.value) for row in check.evidence),
            )
            for check in decision.checks
        ),
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(runs_dir=tmp_path / "runs", cadra_token=SecretStr(""))
    return TestClient(create_app(settings))


def test_decisions_are_identical_across_two_passes(dataset) -> None:
    first = {raw["ticket_id"]: _fingerprint(dataset, raw) for raw in TICKETS}
    second = {raw["ticket_id"]: _fingerprint(dataset, raw) for raw in TICKETS}

    assert first == second
    assert len(first) == 40


def test_api_responses_are_identical_across_runs(client: TestClient) -> None:
    first = client.post("/tickets", json=INBOX).json()
    second = client.post("/tickets", json=INBOX).json()

    def strip_run(responses: list[dict]) -> list[dict]:
        return [
            {key: value for key, value in item.items() if key != "run_id"}
            for item in responses
        ]

    assert strip_run(first) == strip_run(second)


def test_ledger_actions_are_identical_across_runs(client: TestClient) -> None:
    first = client.post("/tickets", json=INBOX).json()
    second = client.post("/tickets", json=INBOX).json()

    def actions(run_id: str) -> list[tuple]:
        entries = client.get("/ledger", params={"run_id": run_id}).json()
        return [
            (entry["ticket_id"], entry["action"], entry["amount_usd"], entry["status"])
            for entry in entries
        ]

    assert actions(first[0]["run_id"]) == actions(second[0]["run_id"])


def test_public_batch_is_deterministic(client: TestClient) -> None:
    first = client.post("/tickets", json=PUBLIC).json()
    second = client.post("/tickets", json=PUBLIC).json()

    assert [
        (item["ticket_id"], item["outcome"], item["reason_codes"])
        for item in first
    ] == [
        (item["ticket_id"], item["outcome"], item["reason_codes"])
        for item in second
    ]
