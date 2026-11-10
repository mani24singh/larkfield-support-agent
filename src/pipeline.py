"""Orchestration: tickets in, schema-valid ``TicketResponse`` plus ledger rows out.

The pipeline keeps HTTP concerns out of policy: it resolves the ticket's own
identity, decides through ``src.policy``, checks refund idempotency in the run
ledger, drafts the customer reply, and records the proposed action.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from src.config import Settings
from src.ingest import Dataset, load_dataset, normalise_email, normalise_order_id
from src.ledger import RefundKey, RunLedger, build_entry, idempotency_key
from src.models import (
    DocumentUsed,
    DraftReply,
    Resolved,
    Ticket,
    TicketResponse,
    Trace,
)
from src.policy import (
    Decision,
    ReasonCode,
    adjudicate_ticket,
    classify_ticket,
    resolve_identity,
    select_affected_lines,
)
from src.tools import TOOL_NAMES

POLICY_DOCUMENT = DocumentUsed(document="refund_policy_RP-3.2.md", version="3.2")

ACTIONABLE_OUTCOMES = frozenset({"PROCEED", "PROCEED_MODIFIED", "PEND"})
NON_ACTIONABLE_OUTCOMES = frozenset(
    {"REJECT", "INVALID_REQUEST", "UNABLE_TO_VERIFY"}
)


@dataclass(frozen=True, slots=True)
class TicketOutcome:
    """Decision plus the identity facts the response and ledger need."""

    decision: Decision
    ticket_type: str
    order_id: str | None
    customer_id: str | None
    refund_keys: tuple[RefundKey, ...]


@lru_cache(maxsize=4)
def _dataset_for(data_dir: str) -> Dataset:
    return load_dataset(data_dir=Path(data_dir))


def dataset_for(settings: Settings) -> Dataset:
    """Return a cached dataset for the configured ``data/`` directory."""
    return _dataset_for(str(Path(settings.data_dir).resolve()))


def evaluate(ticket: Ticket, *, dataset: Dataset, settings: Settings) -> TicketOutcome:
    """Run the adjudicator and derive the refund idempotency keys."""
    decision = adjudicate_ticket(dataset, ticket, settings=settings)
    text = f"{ticket.subject}\n{ticket.body}"
    identity = resolve_identity(
        dataset, email=ticket.from_, order_reference=normalise_order_id(text)
    )

    keys: tuple[RefundKey, ...] = ()
    order = identity.order
    if (
        order is not None
        and decision.amount > Decimal("0.00")
        and decision.outcome in ACTIONABLE_OUTCOMES
    ):
        affected = select_affected_lines(
            order=order,
            lines=dataset.order_lines(order.order_id),
            refunds=dataset.issued_refunds(order.order_id),
            body=text,
        )
        email = normalise_email(ticket.from_)
        keys = tuple(
            idempotency_key(email, order.order_id, item.line_no) for item in affected
        )

    return TicketOutcome(
        decision=decision,
        ticket_type=classify_ticket(subject=ticket.subject, body=ticket.body),
        order_id=order.order_id if order is not None else None,
        customer_id=identity.customer.customer_id
        if identity.customer is not None
        else None,
        refund_keys=keys,
    )


def _describe_reasons(codes: Sequence[ReasonCode]) -> str:
    if ReasonCode.OUT_OF_WINDOW in codes:
        return "It falls outside the 30-day refund window in our refund policy."
    if ReasonCode.ALREADY_REFUNDED in codes:
        return "This item has already been refunded, so no further refund is due."
    if ReasonCode.CUSTOMER_MISMATCH in codes:
        return "We could not match this order to the email address on file."
    if ReasonCode.ORDER_NOT_FOUND in codes:
        return "We could not find that order on our system."
    if ReasonCode.CARRIER_STATUS_UNAVAILABLE in codes:
        return "We are checking with the carrier and will update you shortly."
    if ReasonCode.LOST_IN_TRANSIT in codes:
        return "The carrier has had no movement on this parcel, so it has been escalated."
    if ReasonCode.POLICY_OVERRIDE_ATTEMPT in codes:
        return "Your message has been passed to our trust and safety team."
    return "Our team is reviewing your request."


def build_reply(
    ticket: Ticket,
    outcome: TicketOutcome,
    *,
    duplicate_of: str | None = None,
) -> DraftReply:
    """Draft a customer reply that never promises an unapproved refund."""
    decision = outcome.decision
    amount = f"${decision.amount:,.2f}"

    if duplicate_of is not None:
        body = (
            "Thanks for your email. This request is already being handled under "
            f"ticket {duplicate_of}, so no duplicate refund has been created. "
            "We will be in touch once that request is resolved."
        )
    elif decision.outcome == "PEND":
        body = (
            f"Thank you for your email. We have reviewed your request and proposed "
            f"a refund of {amount}. It is awaiting approval from our support lead, "
            "and we will update you as soon as that review is complete."
        )
    elif decision.outcome == "PROCEED_MODIFIED":
        body = (
            f"Thank you for your email. We can refund {amount}, which covers the "
            "eligible items on your order. Shipping charges and items outside your "
            "request are not included under our refund policy."
        )
    elif decision.outcome == "PROCEED":
        if decision.amount > Decimal("0.00"):
            body = (
                f"Thank you for your email. Your refund of {amount} has been "
                "approved and is being processed."
            )
        else:
            body = (
                "Thank you for your email. We have checked your order and updated "
                "its status for you."
            )
    elif decision.outcome == "ESCALATE":
        role = decision.approver_role or "our specialist team"
        body = (
            "Thank you for your email. This has been escalated to "
            f"{role.replace('_', ' ').title()} for review. {_describe_reasons(decision.reason_codes)}"
        )
    elif decision.outcome == "REJECT":
        body = (
            "Thank you for your email. We are unable to approve this request. "
            f"{_describe_reasons(decision.reason_codes)}"
        )
    elif decision.outcome == "UNABLE_TO_VERIFY":
        body = (
            "Thank you for your email. We cannot verify this request yet. "
            f"{_describe_reasons(decision.reason_codes)}"
        )
    else:  # INVALID_REQUEST
        body = (
            "Thank you for your email. We cannot process this request. "
            f"{_describe_reasons(decision.reason_codes)}"
        )

    return DraftReply(
        to=ticket.from_.strip(),
        subject=f"Re: {ticket.subject}".strip(),
        body=body,
    )


def to_response(
    ticket: Ticket,
    outcome: TicketOutcome,
    *,
    run_id: str,
    duplicate_of: str | None = None,
) -> TicketResponse:
    """Assemble and fully validate one response."""
    decision = outcome.decision
    response = TicketResponse(
        ticket_id=ticket.ticket_id,
        run_id=run_id,
        ticket_type=outcome.ticket_type,
        resolved=Resolved(
            order_id=outcome.order_id, customer_id=outcome.customer_id
        ),
        outcome=decision.outcome,
        reason_codes=[code.value for code in decision.reason_codes],
        checks=list(decision.checks),
        human_approval_required=decision.human_approval_required,
        approver_role=decision.approver_role,
        draft_reply=build_reply(ticket, outcome, duplicate_of=duplicate_of),
        trace=Trace(
            model_calls=[],
            tools_called=list(TOOL_NAMES),
            documents_used=[POLICY_DOCUMENT],
            cost_usd=0.0,
        ),
    )
    return TicketResponse.model_validate(response.model_dump(mode="json"))


def ledger_action(outcome: TicketOutcome) -> tuple[str, Decimal | None] | None:
    """Return the ledger action for an outcome, or ``None`` when nothing is filed."""
    decision = outcome.decision
    if decision.outcome in NON_ACTIONABLE_OUTCOMES:
        return None
    if decision.outcome == "ESCALATE":
        return ("ESCALATION", None)
    if decision.amount > Decimal("0.00"):
        return ("REFUND", decision.amount)
    return ("REPLY", None)


def process_ticket(
    ticket: Ticket,
    *,
    dataset: Dataset,
    ledger: RunLedger,
    run_id: str,
    settings: Settings,
) -> TicketResponse:
    """Process one ticket in input order, suppressing duplicate refunds."""
    outcome = evaluate(ticket, dataset=dataset, settings=settings)

    duplicate_of: str | None = None
    for key in outcome.refund_keys:
        existing = ledger.ticket_for_refund(key)
        if existing is not None:
            duplicate_of = existing
            break

    response = to_response(
        ticket, outcome, run_id=run_id, duplicate_of=duplicate_of
    )
    ledger.add_response(response)

    if duplicate_of is None:
        for key in outcome.refund_keys:
            ledger.register_refund(key, ticket.ticket_id)
        action = ledger_action(outcome)
        if action is not None:
            name, amount = action
            ledger.record(
                build_entry(
                    run_id=run_id,
                    ticket_id=ticket.ticket_id,
                    action=name,
                    amount_usd=amount,
                )
            )

    ledger.save()
    return response


def new_run_id() -> str:
    """Return a unique run identifier, one per submitted batch."""
    return f"run_{uuid.uuid4().hex}"


def process_batch(
    tickets: Sequence[Ticket],
    *,
    dataset: Dataset,
    ledger: RunLedger,
    settings: Settings,
) -> list[TicketResponse]:
    """Process every ticket in the batch order and return validated responses."""
    responses = [
        process_ticket(
            ticket,
            dataset=dataset,
            ledger=ledger,
            run_id=ledger.run_id,
            settings=settings,
        )
        for ticket in tickets
    ]
    ledger.save()
    return responses
