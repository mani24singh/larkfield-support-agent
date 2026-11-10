"""Evaluate the adjudicator against the labelled seed and the full inbox.

Expectations come only from ``tickets/eval_seed_labelled.json``. Nothing here
is relaxed or special-cased per ticket id.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest import Dataset, load_dataset, load_tickets
from src.models import Ticket
from src.policy import adjudicate_ticket, Decision

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
INBOX = ROOT / "tickets" / "support_inbox_export.json"
SEED = ROOT / "tickets" / "eval_seed_labelled.json"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(data_dir=DATA_DIR)


@pytest.fixture(scope="module")
def inbox() -> dict[str, dict]:
    return {ticket["ticket_id"]: ticket for ticket in load_tickets(INBOX)}


@pytest.fixture(scope="module")
def labels() -> list[dict]:
    return list(load_tickets(SEED))


def _adjudicate(dataset: Dataset, raw: dict) -> Decision:
    return adjudicate_ticket(dataset, Ticket.model_validate(raw))


def _diff_table(rows: list[tuple[str, str, object, object]]) -> str:
    headers = ("ticket", "field", "expected", "actual")
    widths = [len(header) for header in headers]
    rendered: list[tuple[str, str, str, str]] = []
    for ticket_id, field, expected, actual in rows:
        cells = (
            ticket_id,
            field,
            f"{expected!r}",
            f"{actual!r}",
        )
        rendered.append(cells)
        widths = [max(width, len(cell)) for width, cell in zip(widths, cells)]

    lines = ["  ".join(header.ljust(width) for header, width in zip(headers, widths))]
    lines.append("  ".join("-" * width for width in widths))
    for cells in rendered:
        lines.append("  ".join(cell.ljust(width) for cell, width in zip(cells, widths)))
    return "\n".join(lines)


def test_all_inbox_tickets_are_adjudicated(dataset: Dataset, inbox: dict) -> None:
    """The adjudicator must handle every public inbox ticket."""
    assert len(inbox) == 30

    failures: list[str] = []
    for ticket_id, raw in inbox.items():
        try:
            decision = _adjudicate(dataset, raw)
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            failures.append(f"{ticket_id}: {type(exc).__name__}: {exc}")
            continue
        assert decision.amount >= Decimal("0.00")
        assert 0 < len(decision.checks) <= 10
    assert not failures, "\n".join(failures)


def test_labelled_tickets_match_expectations(
    dataset: Dataset, inbox: dict, labels: list[dict]
) -> None:
    mismatches: list[tuple[str, str, object, object]] = []

    for label in labels:
        ticket_id = label["ticket_id"]
        decision = _adjudicate(dataset, inbox[ticket_id])

        checks: list[tuple[str, object, object]] = [
            ("outcome", label["expected_outcome"], decision.outcome),
            (
                "human_approval_required",
                label["expected_human_approval_required"],
                decision.human_approval_required,
            ),
            ("approver_role", label["expected_approver_role"], decision.approver_role),
        ]
        if "expected_amount_usd" in label:
            checks.append(
                (
                    "amount_usd",
                    Decimal(str(label["expected_amount_usd"])),
                    decision.amount,
                )
            )
        if "expected_reason_codes" in label:
            checks.append(
                (
                    "reason_codes",
                    set(label["expected_reason_codes"]),
                    {code.value for code in decision.reason_codes},
                )
            )

        for field, expected, actual in checks:
            if expected != actual:
                mismatches.append((ticket_id, field, expected, actual))

    if mismatches:
        pytest.fail(
            f"{len(mismatches)} mismatch(es) across {len(labels)} labelled tickets:\n"
            + _diff_table(mismatches)
        )


def test_labelled_ids_exist_in_inbox(inbox: dict, labels: list[dict]) -> None:
    assert {label["ticket_id"] for label in labels} <= set(inbox)
