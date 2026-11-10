"""Orchestration: tickets in, schema-valid ``TicketResponse`` plus ledger rows out.

Responsibilities split:

* ``evaluate``      — deterministic adjudication and refund idempotency keys.
* ``generate_reply``— advisory model call, strict post-validation, templates.
* ``process_ticket``— duplicate suppression, ledger action, atomic persistence.

The model can only write words. Canonical ids, policy outcomes and every money
figure come from ``src.policy``.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from src.config import Settings
from src.ingest import Dataset, load_dataset, normalise_email, normalise_order_id
from src.ledger import RefundKey, RunLedger, build_entry, idempotency_key
from src.llm import ChatClient, extract_json_object
from src.models import (
    DocumentUsed,
    DraftReply,
    ModelCall,
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
from src.prompts import FALLBACK_TEMPLATES, build_reply_messages

POLICY_DOCUMENT = DocumentUsed(document="refund_policy_RP-3.2.md", version="3.2")

DATA_ACCESS = (
    "ingest.load_dataset",
    "ingest.normalise_email",
    "ingest.normalise_order_id",
    "dataset.customer_by_email",
    "dataset.order",
    "dataset.order_lines",
    "dataset.shipment_for_order",
    "dataset.issued_refunds",
)
"""Data-access operations actually used; recorded as ``trace.tools_called``."""

ACTIONABLE_OUTCOMES = frozenset({"PROCEED", "PROCEED_MODIFIED", "PEND"})
NON_ACTIONABLE_OUTCOMES = frozenset(
    {"REJECT", "INVALID_REQUEST", "UNABLE_TO_VERIFY"}
)
AMOUNT_OUTCOMES = frozenset({"PROCEED", "PROCEED_MODIFIED"})

MIN_WORDS = 90
MAX_WORDS = 160
SIGN_OFF = "Larkfield Home Customer Care"
_DOLLAR_RE = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?")
_RE_PREFIX_RE = re.compile(r"^(?:\s*re\s*:\s*)+", re.IGNORECASE)


class ReplyValidationError(ValueError):
    """Raised when a candidate reply breaks a hard customer-facing rule."""


@dataclass(frozen=True, slots=True)
class TicketOutcome:
    """Decision plus the identity facts the response and ledger need."""

    decision: Decision
    ticket_type: str
    order_id: str | None
    customer_id: str | None
    customer_name: str | None
    refund_keys: tuple[RefundKey, ...]


@dataclass(frozen=True, slots=True)
class ReplyResult:
    """A validated draft plus the real model usage that produced it."""

    draft: DraftReply
    model_calls: tuple[ModelCall, ...]
    cost_usd: float
    used_fallback: bool


@lru_cache(maxsize=4)
def _dataset_for(data_dir: str) -> Dataset:
    return load_dataset(data_dir=Path(data_dir))


def dataset_for(settings: Settings) -> Dataset:
    """Return a cached dataset for the configured ``data/`` directory."""
    return _dataset_for(str(Path(settings.data_dir).resolve()))


def new_run_id() -> str:
    """Return a unique run identifier, one per submitted batch."""
    return f"run_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


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
        customer_name=identity.customer.name if identity.customer is not None else None,
        refund_keys=keys,
    )


# ---------------------------------------------------------------------------
# Reply: prompt, validation, fallback
# ---------------------------------------------------------------------------


def amount_allowed(decision: Decision) -> bool:
    """A dollar figure may be stated only for an approved refund."""
    return (
        decision.outcome in AMOUNT_OUTCOMES
        and decision.amount > Decimal("0.00")
    )


def first_name_for(ticket: Ticket, outcome: TicketOutcome) -> str:
    """Customer first name, falling back to the sender's local part."""
    if outcome.customer_name:
        return outcome.customer_name.split()[0]
    local = ticket.from_.strip().split("@", 1)[0]
    token = re.split(r"[._+-]", local.strip())[0]
    return token.capitalize() or "there"


def reply_subject(original: str) -> str:
    """Return ``Re: <subject>`` without doubling an existing ``Re:``."""
    stripped = _RE_PREFIX_RE.sub("", original.strip())
    return f"Re: {stripped or 'your message'}"


def amount_clause(decision: Decision) -> str:
    return f"${decision.amount:,.2f}" if amount_allowed(decision) else ""


def describe_reasons(codes: Sequence[ReasonCode]) -> str:
    """A customer-safe sentence with no internal codes or amounts."""
    if ReasonCode.OUT_OF_WINDOW in codes:
        return "It falls outside the thirty-day window allowed by our refund policy."
    if ReasonCode.ALREADY_REFUNDED in codes:
        return "The item on this order has already been refunded in full."
    if ReasonCode.CUSTOMER_MISMATCH in codes:
        return "We could not match this order to the email address on file."
    if ReasonCode.ORDER_NOT_FOUND in codes:
        return "We could not find that order in our records."
    if ReasonCode.CARRIER_STATUS_UNAVAILABLE in codes:
        return "We are checking the latest details with the carrier."
    if ReasonCode.LOST_IN_TRANSIT in codes:
        return "The carrier has recorded no movement on this parcel."
    if ReasonCode.POLICY_OVERRIDE_ATTEMPT in codes:
        return "Your message has been passed to our specialist team."
    if ReasonCode.APPROVAL_REQUIRED in codes:
        return "It needs approval from a support lead before we can continue."
    if ReasonCode.PARTIAL_LINES in codes:
        return "Only the affected item is covered by our refund policy."
    return "Our team is reviewing the details of your request."


def _word_count(body: str) -> int:
    return len(body.split())


def validate_reply(
    body: str, *, decision: Decision, first_name: str
) -> str:
    """Enforce the customer-facing rules on a candidate reply.

    Raises:
        ReplyValidationError: if the text breaks any hard rule.
    """
    cleaned = body.strip()
    if not cleaned:
        raise ReplyValidationError("empty reply")

    words = _word_count(cleaned)
    if not MIN_WORDS <= words <= MAX_WORDS:
        raise ReplyValidationError(f"word count {words} outside {MIN_WORDS}-{MAX_WORDS}")

    if not re.search(rf"\b{re.escape(first_name)}\b", cleaned, re.IGNORECASE):
        raise ReplyValidationError("customer first name missing")

    if SIGN_OFF not in cleaned:
        raise ReplyValidationError("sign-off missing")

    figures = _DOLLAR_RE.findall(cleaned)
    if amount_allowed(decision):
        if not figures:
            raise ReplyValidationError("approved amount is missing")
        for figure in figures:
            digits = figure.replace("$", "").replace(",", "").strip()
            try:
                value = Decimal(digits)
            except Exception as exc:  # noqa: BLE001 - any unparsable figure is invalid
                raise ReplyValidationError(f"unparsable amount {figure!r}") from exc
            if value != decision.amount:
                raise ReplyValidationError(
                    f"amount {value} does not match decision amount {decision.amount}"
                )
    elif figures:
        raise ReplyValidationError("reply states an amount that is not allowed")

    lowered = cleaned.lower()
    if (
        re.search(r"\bai\b", cleaned, re.IGNORECASE)
        or "policy engine" in lowered
        or "automated system" in lowered
    ):
        raise ReplyValidationError("reply mentions AI or automation")
    for code in ReasonCode:
        if code.value in cleaned:
            raise ReplyValidationError(f"reply exposes internal code {code.value}")

    return cleaned


def _render_fallback(
    ticket: Ticket,
    outcome: TicketOutcome,
    *,
    first_name: str,
    duplicate_of: str | None,
) -> str:
    decision = outcome.decision
    detail = describe_reasons(decision.reason_codes)

    if duplicate_of is not None:
        if amount_allowed(decision):
            detail = (
                f"A refund of {amount_clause(decision)} has already been approved "
                "and is being processed."
            )
        else:
            detail = "Our team is already looking into the original request."
        template = FALLBACK_TEMPLATES["DUPLICATE"]
        body = template.format(first=first_name, detail=detail)
    elif decision.outcome in {"PEND", "REJECT", "ESCALATE", "INVALID_REQUEST", "UNABLE_TO_VERIFY"}:
        body = FALLBACK_TEMPLATES[decision.outcome].format(
            first=first_name, detail=detail
        )
    elif decision.outcome == "PROCEED_MODIFIED":
        body = FALLBACK_TEMPLATES["PROCEED_MODIFIED"].format(
            first=first_name, amount=amount_clause(decision), detail=detail
        )
    elif decision.outcome == "PROCEED" and amount_allowed(decision):
        body = FALLBACK_TEMPLATES["PROCEED_REFUND"].format(
            first=first_name, amount=amount_clause(decision), detail=detail
        )
    else:
        body = FALLBACK_TEMPLATES["PROCEED_STATUS"].format(
            first=first_name, detail=detail
        )
    return body


def generate_reply(
    ticket: Ticket,
    outcome: TicketOutcome,
    *,
    settings: Settings,
    client: ChatClient | None = None,
    duplicate_of: str | None = None,
) -> ReplyResult:
    """Produce a validated draft, using the model only when it improves nothing.

    The model is advisory: it cannot change the decision, and any output that
    breaks the customer-facing rules is discarded for a template.
    """
    first_name = first_name_for(ticket, outcome)
    subject = reply_subject(ticket.subject)

    fallback_body = _render_fallback(
        ticket, outcome, first_name=first_name, duplicate_of=duplicate_of
    )
    validate_reply(
        fallback_body, decision=outcome.decision, first_name=first_name
    )
    fallback = ReplyResult(
        draft=DraftReply(
            to=ticket.from_.strip(), subject=subject, body=fallback_body
        ),
        model_calls=(),
        cost_usd=0.0,
        used_fallback=True,
    )

    if duplicate_of is not None or client is None:
        return fallback

    allowed = outcome.decision.amount if amount_allowed(outcome.decision) else None
    try:
        result = client.complete(
            build_reply_messages(
                ticket,
                outcome=outcome.decision.outcome,
                ticket_type=outcome.ticket_type,
                reason_detail=describe_reasons(outcome.decision.reason_codes),
                first_name=first_name,
                allowed_amount=allowed,
            ),
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 - any provider failure falls back
        return fallback

    call = ModelCall(
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    try:
        payload = extract_json_object(result.text)
        body = validate_reply(
            str(payload.get("body", "")),
            decision=outcome.decision,
            first_name=first_name,
        )
    except Exception:  # noqa: BLE001 - invalid model output falls back
        return ReplyResult(
            draft=fallback.draft,
            model_calls=(call,),
            cost_usd=result.cost_usd,
            used_fallback=True,
        )

    return ReplyResult(
        draft=DraftReply(to=ticket.from_.strip(), subject=subject, body=body),
        model_calls=(call,),
        cost_usd=result.cost_usd,
        used_fallback=False,
    )


# ---------------------------------------------------------------------------
# Response and ledger
# ---------------------------------------------------------------------------


def to_response(
    ticket: Ticket,
    outcome: TicketOutcome,
    reply: ReplyResult,
    *,
    run_id: str,
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
        draft_reply=reply.draft,
        trace=Trace(
            model_calls=list(reply.model_calls),
            tools_called=list(DATA_ACCESS),
            documents_used=[POLICY_DOCUMENT],
            cost_usd=reply.cost_usd,
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
    client: ChatClient | None = None,
) -> TicketResponse:
    """Process one ticket in input order, suppressing duplicate refunds."""
    outcome = evaluate(ticket, dataset=dataset, settings=settings)

    duplicate_of: str | None = None
    for key in outcome.refund_keys:
        existing = ledger.ticket_for_refund(key)
        if existing is not None:
            duplicate_of = existing
            break

    reply = generate_reply(
        ticket,
        outcome,
        settings=settings,
        client=client,
        duplicate_of=duplicate_of,
    )
    response = to_response(ticket, outcome, reply, run_id=run_id)
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


def process_batch(
    tickets: Sequence[Ticket],
    *,
    dataset: Dataset,
    ledger: RunLedger,
    settings: Settings,
    client: ChatClient | None = None,
) -> list[TicketResponse]:
    """Process every ticket in the batch order and return validated responses."""
    responses = [
        process_ticket(
            ticket,
            dataset=dataset,
            ledger=ledger,
            run_id=ledger.run_id,
            settings=settings,
            client=client,
        )
        for ticket in tickets
    ]
    ledger.save()
    return responses
