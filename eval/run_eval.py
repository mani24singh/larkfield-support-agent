#!/usr/bin/env python
"""Offline evaluation of the support agent.

Runs all 40 public tickets through the full pipeline, validates every response
against the supplied schema, compares the labelled seed, checks determinism
across two independent passes, and checks refund idempotency across a repeated
run. Writes ``reports/eval_report.md`` and exits non-zero if anything fails.

Usage:
    python eval/run_eval.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import SecretStr  # noqa: E402

from src.config import Settings  # noqa: E402
from src.ingest import load_dataset, load_tickets  # noqa: E402
from src.ledger import RunLedger  # noqa: E402
from src.models import Ticket, TicketResponse  # noqa: E402
from src.pipeline import evaluate, new_run_id, process_batch  # noqa: E402

REPORTS_DIR = ROOT / "reports"
INBOX_PATH = ROOT / "tickets" / "support_inbox_export.json"
PUBLIC_PATH = ROOT / "inbox" / "public_tickets.json"
SEED_PATH = ROOT / "tickets" / "eval_seed_labelled.json"

ACTIONABLE = {"PROCEED", "PROCEED_MODIFIED", "PEND"}


def _settings() -> Settings:
    return Settings(runs_dir=ROOT / "runs", cadra_token=SecretStr(""))


def _fingerprint(dataset, ticket: Ticket) -> tuple:
    decision = evaluate(ticket, dataset=dataset, settings=_settings()).decision
    return (
        decision.outcome,
        tuple(code.value for code in decision.reason_codes),
        str(decision.amount),
        decision.approver_role,
        decision.human_approval_required,
        tuple(
            (
                check.check_id,
                check.result,
                check.policy_clause,
                tuple((row.source, row.ref, row.value) for row in check.evidence),
            )
            for check in decision.checks
        ),
    )


def _run_batch(dataset, tickets: list[Ticket]) -> tuple[str, list[TicketResponse], RunLedger]:
    run_id = new_run_id()
    ledger = RunLedger(run_id, _settings().runs_dir / run_id)
    responses = process_batch(
        tickets,
        dataset=dataset,
        ledger=ledger,
        settings=_settings(),
    )
    return run_id, responses, ledger


def _check_label(label: dict, decision) -> list[tuple[str, object, object]]:
    mismatches: list[tuple[str, object, object]] = []
    pairs: list[tuple[str, object, object]] = [
        ("outcome", label["expected_outcome"], decision.outcome),
        (
            "human_approval_required",
            label["expected_human_approval_required"],
            decision.human_approval_required,
        ),
        ("approver_role", label["expected_approver_role"], decision.approver_role),
    ]
    if "expected_amount_usd" in label:
        pairs.append(
            (
                "amount_usd",
                Decimal(str(label["expected_amount_usd"])),
                decision.amount,
            )
        )
    if "expected_reason_codes" in label:
        pairs.append(
            (
                "reason_codes",
                set(label["expected_reason_codes"]),
                {code.value for code in decision.reason_codes},
            )
        )
    for field, expected, actual in pairs:
        if expected != actual:
            mismatches.append((field, expected, actual))
    return mismatches


def _cell(value: object) -> str:
    return str(value) if value not in (None, "") else "\u2014"


def main() -> int:
    dataset = load_dataset(data_dir=ROOT / "data")
    raw_tickets = list(load_tickets(INBOX_PATH)) + list(load_tickets(PUBLIC_PATH))
    labels = {row["ticket_id"]: row for row in load_tickets(SEED_PATH)}
    tickets = [Ticket.model_validate(raw) for raw in raw_tickets]

    settings = _settings()

    started = time.perf_counter()
    run_id, responses, ledger = _run_batch(dataset, tickets)
    runtime = time.perf_counter() - started

    decisions = {
        ticket.ticket_id: evaluate(ticket, dataset=dataset, settings=settings).decision
        for ticket in tickets
    }

    # -- schema -----------------------------------------------------------
    schema_failures: list[str] = []
    for response in responses:
        try:
            TicketResponse.model_validate(response.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - report every failure
            schema_failures.append(f"{response.ticket_id}: {type(exc).__name__}: {exc}")

    # -- labelled seed ----------------------------------------------------
    label_results: dict[str, str] = {}
    label_mismatches: list[tuple[str, str, object, object]] = []
    for ticket_id, label in labels.items():
        decision = decisions[ticket_id]
        mismatches = _check_label(label, decision)
        if mismatches:
            label_results[ticket_id] = "FAIL"
            label_mismatches.extend(
                (ticket_id, field, expected, actual)
                for field, expected, actual in mismatches
            )
        else:
            label_results[ticket_id] = "PASS"

    # -- determinism ------------------------------------------------------
    first_pass = {ticket.ticket_id: _fingerprint(dataset, ticket) for ticket in tickets}
    second_pass = {ticket.ticket_id: _fingerprint(dataset, ticket) for ticket in tickets}
    determinism_diffs = [
        ticket_id
        for ticket_id in first_pass
        if first_pass[ticket_id] != second_pass[ticket_id]
    ]

    # -- idempotency across a repeated run --------------------------------
    replay_run_id, replay_responses, replay_ledger = _run_batch(dataset, tickets + tickets)
    single_refunds = [entry for entry in ledger.entries if entry.action == "REFUND"]
    replay_refunds = [entry for entry in replay_ledger.entries if entry.action == "REFUND"]

    first_half = {ticket.ticket_id: decisions[ticket.ticket_id] for ticket in tickets}
    repeated = replay_responses[len(tickets):]
    expected_duplicates = 0
    duplicate_reply_failures: list[str] = []
    for ticket in tickets:
        decision = first_half[ticket.ticket_id]
        if decision.outcome in ACTIONABLE and decision.amount > Decimal("0.00"):
            expected_duplicates += 1

    for original, repeat in zip(tickets, repeated):
        decision = first_half[original.ticket_id]
        should_duplicate = (
            decision.outcome in ACTIONABLE and decision.amount > Decimal("0.00")
        )
        says_duplicate = "already being handled" in repeat.draft_reply.body.lower()
        if should_duplicate and not says_duplicate:
            duplicate_reply_failures.append(original.ticket_id)

    idempotency_ok = (
        len(replay_refunds) == len(single_refunds) and not duplicate_reply_failures
    )

    # -- cost -------------------------------------------------------------
    total_cost = sum(response.trace.cost_usd for response in responses)

    # -- report -----------------------------------------------------------
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORTS_DIR / "eval_report.md"
    outcome_counts = Counter(
        response.outcome for response in responses
    )
    all_ok = (
        not schema_failures
        and not label_mismatches
        and not determinism_diffs
        and idempotency_ok
    )

    lines: list[str] = [
        "# Evaluation report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Run: `{run_id}` (replay `{replay_run_id}`)",
        f"Runtime: {runtime:.2f}s",
        "",
        "## Headline metrics",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Tickets processed | {len(responses)} / 40 |",
        f"| Schema-valid responses | {len(responses) - len(schema_failures)} / {len(responses)} |",
        f"| Labelled tickets matched | {sum(1 for v in label_results.values() if v == 'PASS')} / {len(labels)} |",
        f"| Deterministic across two passes | {len(tickets) - len(determinism_diffs)} / {len(tickets)} |",
        f"| Idempotent replay | {'PASS' if idempotency_ok else 'FAIL'} |",
        f"| Refund actions (single / replay) | {len(single_refunds)} / {len(replay_refunds)} |",
        f"| Expected duplicate replies | {expected_duplicates} |",
        f"| Model cost (USD) | {total_cost:.4f} |",
        f"| Overall | {'PASS' if all_ok else 'FAIL'} |",
        "",
        "### Outcome distribution",
        "",
        "| Outcome | Count |",
        "|---|---|",
    ]
    for outcome, count in sorted(outcome_counts.items()):
        lines.append(f"| {outcome} | {count} |")

    lines.extend(["", "## Failures", ""])
    failures: list[str] = []
    failures.extend(f"schema: {item}" for item in schema_failures)
    failures.extend(
        f"label {ticket_id}: {field} expected {expected!r}, got {actual!r}"
        for ticket_id, field, expected, actual in label_mismatches
    )
    failures.extend(f"determinism: {ticket_id} differed" for ticket_id in determinism_diffs)
    failures.extend(
        f"idempotency: {ticket_id} repeated without a duplicate reply"
        for ticket_id in duplicate_reply_failures
    )
    if len(replay_refunds) != len(single_refunds):
        failures.append(
            "idempotency: replay created "
            f"{len(replay_refunds) - len(single_refunds)} extra refund action(s)"
        )
    lines.extend([f"- {item}" for item in failures] if failures else ["None."])

    lines.extend(
        [
            "",
            "## Per-ticket results",
            "",
            "| Ticket | Type | Outcome | Approval | Approver | Amount (USD) | Reason codes | Label |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for response in responses:
        ticket_id = response.ticket_id
        decision = decisions[ticket_id]
        lines.append(
            "| {ticket} | {type} | {outcome} | {approval} | {role} | {amount} | {codes} | {label} |".format(
                ticket=ticket_id,
                type=response.ticket_type,
                outcome=response.outcome,
                approval="yes" if response.human_approval_required else "no",
                role=_cell(response.approver_role),
                amount=f"{decision.amount:.2f}"
                if decision.amount > Decimal("0.00")
                else "\u2014",
                codes=", ".join(response.reason_codes) or "\u2014",
                label=label_results.get(ticket_id, "\u2014"),
            )
        )

    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Tickets processed : {len(responses)}/40")
    print(f"Schema valid      : {len(responses) - len(schema_failures)}/{len(responses)}")
    print(
        "Labelled matched  : "
        f"{sum(1 for value in label_results.values() if value == 'PASS')}/{len(labels)}"
    )
    print(f"Deterministic     : {len(tickets) - len(determinism_diffs)}/{len(tickets)}")
    print(
        f"Idempotent replay : {'PASS' if idempotency_ok else 'FAIL'} "
        f"({len(single_refunds)} refunds single, {len(replay_refunds)} replay)"
    )
    print(f"Overall           : {'PASS' if all_ok else 'FAIL'}")
    print(f"Report            : {report}")
    if failures:
        print("\nFailures:")
        for item in failures:
            print(f" - {item}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
