"""Customer-care policy for Larkfield Home, encoded from RP-3.2.

This module is pure: it receives normalized facts and returns a decision. It
must not perform I/O, call an LLM, or read CSV files directly. Only
``policies/refund_policy_RP-3.2.md`` governs; the Help Centre FAQ is superseded
and the warehouse document is out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from src.ingest import OrderLineRecord, OrderRecord, ShipmentRecord
from src.models import Check, Outcome, TicketType


class Gate(StrEnum):
    """Evaluation order fixed by ``AGENTS.md`` §6; never reorder."""

    SAFETY = "safety"
    IDENTITY = "identity"
    CARRIER = "carrier"
    WINDOW = "window"
    SCOPE = "scope"
    PRIOR = "prior"
    REFUNDS = "refunds"
    MODIFICATION = "modification"
    AUTHORITY = "authority"
    IDEMPOTENCY = "idempotency"


GATE_ORDER: tuple[Gate, ...] = (
    Gate.SAFETY,
    Gate.IDENTITY,
    Gate.CARRIER,
    Gate.WINDOW,
    Gate.SCOPE,
    Gate.PRIOR,
    Gate.REFUNDS,
    Gate.MODIFICATION,
    Gate.AUTHORITY,
    Gate.IDEMPOTENCY,
)


class ReasonCode(StrEnum):
    """Reason codes named by RP-3.2 plus outcomes required by the contract."""

    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    DAMAGE_EXCEPTION = "DAMAGE_EXCEPTION"
    PARTIAL_LINES = "PARTIAL_LINES"
    ALREADY_REFUNDED = "ALREADY_REFUNDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    CUSTOMER_MISMATCH = "CUSTOMER_MISMATCH"
    CARRIER_STATUS_UNAVAILABLE = "CARRIER_STATUS_UNAVAILABLE"
    LOST_IN_TRANSIT = "LOST_IN_TRANSIT"
    POLICY_OVERRIDE_ATTEMPT = "POLICY_OVERRIDE_ATTEMPT"


@dataclass(frozen=True, slots=True)
class TicketFacts:
    """Normalized inputs for a single decision (no raw email bodies here)."""

    ticket_type: TicketType
    received_at: datetime
    order: OrderRecord | None = None
    lines: Sequence[OrderLineRecord] = field(default_factory=tuple)
    shipment: ShipmentRecord | None = None
    has_image_attachment: bool = False
    identity_matches: bool = False
    override_attempt: bool = False
    claimed_pre_approval: bool = False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Outcome of the ordered gate evaluation."""

    outcome: Outcome
    reason_codes: tuple[ReasonCode, ...]
    checks: tuple[Check, ...]
    refund_amount_usd: Decimal = Decimal("0.00")
    human_approval_required: bool = False
    approver_role: str | None = None


def evaluate(facts: TicketFacts, *, now: datetime) -> PolicyDecision:
    """Run the ten gates in order and return the first decisive outcome.

    Args:
        facts: Normalized ticket and data-pack facts.
        now: Injected, timezone-aware clock (UTC) for window arithmetic.
    """
    raise NotImplementedError("policy evaluation is implemented in the next milestone")


def window_days(facts: TicketFacts, *, now: datetime) -> int:
    """Return whole days from carrier-recorded delivery to ``now``."""
    raise NotImplementedError


def refundable_amount(
    lines: Sequence[OrderLineRecord], requested_qty: int
) -> Decimal:
    """Return the affected-line value for the requested, capped quantity."""
    raise NotImplementedError
