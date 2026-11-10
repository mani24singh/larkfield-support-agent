"""Customer-care policy for Larkfield Home, encoded from RP-3.2.

This module is pure: no I/O, no clock reads, no LLM calls. Callers pass in
normalised records from ``src.ingest`` and an explicit ``now``/``received_at``.
Only ``policies/refund_policy_RP-3.2.md`` governs; the Help Centre FAQ is
superseded and the warehouse document is out of scope.

Email text is untrusted **data**. Nothing in a ticket is executed, and any text
that tries to instruct the service is reported as a policy-integrity failure
rather than obeyed.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum

from src.config import Settings
from src.ingest import (
    CustomerRecord,
    Dataset,
    OrderLineRecord,
    OrderRecord,
    RefundRecord,
    ShipmentRecord,
    normalise_order_id,
    parse_timestamp,
)
from src.money import quantize_money
from src.models import (
    ApproverRole,
    Attachment,
    Check,
    CheckResult,
    Evidence,
    Outcome,
    Ticket,
    TicketType,
)

BASE_CHECK_ORDER: tuple[str, ...] = (
    "POLICY_INTEGRITY",
    "ORDER_IDENTIFIED",
    "CUSTOMER_MATCH",
    "CARRIER_STATUS",
    "CARRIER_MOVEMENT",
    "DELIVERY_WINDOW",
    "DAMAGE_EVIDENCE",
)
MONEY_CHECK_ORDER: tuple[str, ...] = ("ITEM_SCOPE", "PRIOR_REFUNDS", "AUTHORITY")

CHECK_ORDER: tuple[str, ...] = BASE_CHECK_ORDER + MONEY_CHECK_ORDER
"""Check emission order required by the brief; ``assess_refund`` follows it."""

POLICY_DOCUMENT = "refund_policy_RP-3.2.md"
POLICY_VERSION = "3.2"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp")


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
    """Reason codes named by RP-3.2."""

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


# ---------------------------------------------------------------------------
# 1. Policy integrity (gate 1, RP-3.2 5.2)
# ---------------------------------------------------------------------------

_OVERRIDE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bypass",
        re.compile(
            r"\b(?:ignore|bypass|override|disregard|circumvent|skip|get around)\b"
            r"[^.\n]{0,60}?\b(?:policy|policies|rules?|system|instructions?|guidelines?|checks?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_override",
        re.compile(
            r"\b(?:system|developer|admin|internal)\s*(?:prompt|override|instruction)\b"
            r"|\bignore (?:all|any|the|your) (?:previous|prior|above) (?:instructions?|rules?)\b"
            r"|\bnote to the (?:ai|assistant|agent|system)\b"
            r"|\[system\]",
            re.IGNORECASE,
        ),
    ),
    (
        "role_change",
        re.compile(
            r"\byou are now\b|\bpretend (?:that )?you(?: are)?\b"
            r"|\b(?:you are|you're)\s+(?:now\s+)?(?:authorised|authorized|permitted|allowed)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "limit_override",
        re.compile(
            r"\b(?:above|over|beyond|regardless of|ignoring)\s+(?:any|the|all|your)?\s*"
            r"(?:limits?|thresholds?|policy|policies)\b"
            r"|\bwithout (?:any )?(?:review|approval|checks?)\b",
            re.IGNORECASE,
        ),
    ),
    ("pre_approval", re.compile(r"\bpre[-\s]?approved\b|\balready approved\b", re.IGNORECASE)),
    ("chargeback", re.compile(r"\bcharge\s?back\b|\bchargeback\b", re.IGNORECASE)),
    (
        "legal_threat",
        re.compile(
            r"\b(?:legal action|lawyer|attorney|lawsuit|sue (?:you|us)|take you to court|small claims)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(?:i am|i'm|this is) (?:the |a )?(?:ceo|owner|manager|head of|director)\b"
            r"|\bas (?:the )?(?:ceo|owner|manager)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "urgency_command",
        re.compile(
            r"\b(?:do it|refund (?:me|it|this)|approve (?:it|this))\s+"
            r"(?:now|immediately|right now|or else)\b",
            re.IGNORECASE,
        ),
    ),
)


def detect_policy_override(text: str) -> str | None:
    """Return the evidence phrase when the email tries to override policy.

    The email is treated purely as data: this only pattern-matches and returns
    the matched phrase verbatim, preserving the customer's casing for evidence.
    """
    if not text:
        return None
    for _name, pattern in _OVERRIDE_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return match.group(0).strip()
    return None


# ---------------------------------------------------------------------------
# 2. Identity (gate 2, RP-3.2 4.1)
# ---------------------------------------------------------------------------


class IdentityStatus(StrEnum):
    """How far identity resolution got."""

    RESOLVED = "RESOLVED"
    NO_REFERENCE = "NO_REFERENCE"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    UNKNOWN_CUSTOMER = "UNKNOWN_CUSTOMER"
    CUSTOMER_MISMATCH = "CUSTOMER_MISMATCH"


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """Canonical order, matched customer, and whether the sender owns it."""

    status: IdentityStatus
    canonical_order_id: str | None
    order: OrderRecord | None
    customer: CustomerRecord | None
    owner_verified: bool
    reason_code: ReasonCode | None = None

    @property
    def order_identified(self) -> bool:
        """True only when the canonical order exists in the data pack."""
        return self.order is not None


def resolve_identity(
    dataset: Dataset, *, email: str, order_reference: str | None
) -> IdentityResolution:
    """Resolve the order and its owner from the sender's email (RP-3.2 4.1).

    Never substitutes a similar or nearby order: an unparseable or unknown
    reference resolves to ``None`` rather than a guess.
    """
    canonical = normalise_order_id(order_reference)

    if canonical is None:
        return IdentityResolution(
            status=IdentityStatus.NO_REFERENCE,
            canonical_order_id=None,
            order=None,
            customer=dataset.customer_by_email(email),
            owner_verified=False,
            reason_code=ReasonCode.ORDER_NOT_FOUND,
        )

    order = dataset.order(canonical)
    if order is None:
        return IdentityResolution(
            status=IdentityStatus.UNKNOWN_ORDER,
            canonical_order_id=canonical,
            order=None,
            customer=dataset.customer_by_email(email),
            owner_verified=False,
            reason_code=ReasonCode.ORDER_NOT_FOUND,
        )

    customer = dataset.customer_by_email(email)
    if customer is None:
        return IdentityResolution(
            status=IdentityStatus.UNKNOWN_CUSTOMER,
            canonical_order_id=canonical,
            order=order,
            customer=None,
            owner_verified=False,
            reason_code=ReasonCode.CUSTOMER_MISMATCH,
        )

    if order.customer_id != customer.customer_id:
        return IdentityResolution(
            status=IdentityStatus.CUSTOMER_MISMATCH,
            canonical_order_id=canonical,
            order=order,
            customer=customer,
            owner_verified=False,
            reason_code=ReasonCode.CUSTOMER_MISMATCH,
        )

    return IdentityResolution(
        status=IdentityStatus.RESOLVED,
        canonical_order_id=canonical,
        order=order,
        customer=customer,
        owner_verified=True,
    )


# ---------------------------------------------------------------------------
# 3. Ticket type
# ---------------------------------------------------------------------------

_REFUND_HINTS = re.compile(
    r"\brefund\b|\bmoney back\b|\breimburse\b|\brepayment\b|\bcredit (?:me|my card)\b",
    re.IGNORECASE,
)
_RETURN_HINTS = re.compile(
    r"\breturn\b|\bsend (?:it|them|the \w+) back\b|\bexchange\b|\bcollection\b",
    re.IGNORECASE,
)
_DELIVERY_FAILURE_HINTS = re.compile(
    r"\b(?:never|not|hasn't|has not|didn't|did not)\s+(?:arrived|turned up|been delivered)\b"
    r"|\bmissing\b|\bstill waiting\b|\bnot received\b|\bno delivery\b|\bshowing as delivered\b"
    r"|\bdamaged in transit\b|\blost (?:parcel|package|order)\b",
    re.IGNORECASE,
)
_ORDER_STATUS_HINTS = re.compile(
    r"\bwhere (?:is|are) my (?:order|parcel|package|items?)\b|\btracking\b|\bstatus\b"
    r"|\bwhen (?:will|should)\b[^.\n]{0,30}\b(?:arrive|be delivered|ship)\b"
    r"|\bdispatched\b|\bshipped\b|\bon its way\b",
    re.IGNORECASE,
)


def classify_ticket(*, subject: str = "", body: str = "") -> TicketType:
    """Classify the email into one of the five documented ticket types.

    Precedence: an explicit money-back ask is ``REFUND``; a return/exchange ask
    is ``RETURN``; failed delivery is ``DELIVERY_ISSUE``; a tracking question is
    ``ORDER_STATUS``; everything else is ``OTHER``.
    """
    text = f"{subject}\n{body}"
    if _REFUND_HINTS.search(text):
        return "REFUND"
    if _RETURN_HINTS.search(text):
        return "RETURN"
    if _DELIVERY_FAILURE_HINTS.search(text):
        return "DELIVERY_ISSUE"
    if _ORDER_STATUS_HINTS.search(text):
        return "ORDER_STATUS"
    return "OTHER"


# ---------------------------------------------------------------------------
# 4. Carrier (gate 3, RP-3.2 4.2 and 5.1)
# ---------------------------------------------------------------------------


class CarrierState(StrEnum):
    """What the carrier feed lets us say about the shipment."""

    DELIVERED = "DELIVERED"
    MOVING = "MOVING"
    LOST = "LOST"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CarrierEvaluation:
    """Carrier status plus movement evidence; never guesses when data is absent."""

    state: CarrierState
    days_since_last_scan: int | None
    delivered_at: datetime | None
    reason_code: ReasonCode | None = None
    evidence: tuple[Evidence, ...] = ()

    @property
    def status_verified(self) -> bool:
        """A verified status excludes the UNAVAILABLE state."""
        return self.state is not CarrierState.UNAVAILABLE

    @property
    def is_lost(self) -> bool:
        return self.state is CarrierState.LOST

    @property
    def is_delivered(self) -> bool:
        return self.state is CarrierState.DELIVERED


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def evaluate_carrier(
    shipment: ShipmentRecord | None,
    *,
    now: datetime,
    settings: Settings | None = None,
) -> CarrierEvaluation:
    """Evaluate the carrier record: available, moving, delivered or lost.

    ``UNAVAILABLE`` (or a missing row) means a non-delivery claim cannot be
    verified (RP-3.2 4.2). In transit with no scan for more than ten days means
    lost in transit (RP-3.2 5.1). Movement is measured from the latest carrier
    event, falling back to ``shipped_at`` when the feed is inconsistent.
    """
    config = settings or Settings()
    moment = _as_utc(now)

    if shipment is None:
        return CarrierEvaluation(
            state=CarrierState.UNAVAILABLE,
            days_since_last_scan=None,
            delivered_at=None,
            reason_code=ReasonCode.CARRIER_STATUS_UNAVAILABLE,
            evidence=(
                Evidence(source="shipments.csv", ref="(none)", value="no shipment record"),
            ),
        )

    status = shipment.status.strip().upper()
    delivered_at = shipment.delivered_at
    last_event = shipment.last_event_at
    # Prefer the carrier's latest scan. Falling back to shipped_at only when the
    # feed is empty; a scan older than shipped_at still means the feed is stale.
    last_scan = last_event if last_event is not None else shipment.shipped_at
    last_scan = _as_utc(last_scan) if last_scan is not None else None

    evidence: list[Evidence] = [
        Evidence(source="shipments.csv", ref=shipment.shipment_id, value=f"status={status}"),
        Evidence(
            source="shipments.csv",
            ref=shipment.shipment_id,
            value=f"last_event_at={last_event.isoformat() if last_event else 'none'}",
        ),
    ]

    if status == "UNAVAILABLE" or (status == "DELIVERED" and delivered_at is None):
        return CarrierEvaluation(
            state=CarrierState.UNAVAILABLE,
            days_since_last_scan=None,
            delivered_at=None,
            reason_code=ReasonCode.CARRIER_STATUS_UNAVAILABLE,
            evidence=tuple(evidence),
        )

    if status == "DELIVERED":
        return CarrierEvaluation(
            state=CarrierState.DELIVERED,
            days_since_last_scan=(
                (moment - _as_utc(delivered_at)).days if delivered_at is not None else None
            ),
            delivered_at=_as_utc(delivered_at) if delivered_at is not None else None,
            evidence=tuple(evidence),
        )

    if status == "IN_TRANSIT":
        if last_scan is None:
            return CarrierEvaluation(
                state=CarrierState.UNAVAILABLE,
                days_since_last_scan=None,
                delivered_at=None,
                reason_code=ReasonCode.CARRIER_STATUS_UNAVAILABLE,
                evidence=tuple(evidence),
            )

        stalled_days = (moment - last_scan).days
        stalled = moment - last_scan
        if stalled > timedelta(days=config.lost_in_transit_days):
            return CarrierEvaluation(
                state=CarrierState.LOST,
                days_since_last_scan=stalled_days,
                delivered_at=None,
                reason_code=ReasonCode.LOST_IN_TRANSIT,
                evidence=tuple(
                    evidence
                    + [
                        Evidence(
                            source="policy:RP-3.2",
                            ref="5.1",
                            value=(
                                f"no scan for {stalled} "
                                f"(> {config.lost_in_transit_days} days)"
                            ),
                        )
                    ]
                ),
            )

        return CarrierEvaluation(
            state=CarrierState.MOVING,
            days_since_last_scan=stalled_days,
            delivered_at=None,
            evidence=tuple(
                evidence
                + [
                    Evidence(
                        source="policy:RP-3.2",
                        ref="5.3",
                        value=f"last scan {stalled_days} days ago",
                    )
                ]
            ),
        )

    return CarrierEvaluation(
        state=CarrierState.UNAVAILABLE,
        days_since_last_scan=None,
        delivered_at=None,
        reason_code=ReasonCode.CARRIER_STATUS_UNAVAILABLE,
        evidence=tuple(evidence),
    )


# ---------------------------------------------------------------------------
# 5. Delivery window (gate 4, RP-3.2 2.1, 2.3, 2.6)
# ---------------------------------------------------------------------------

_DAMAGE_HINTS = re.compile(
    r"\bdamaged?\b|\bbroken\b|\bfaulty\b|\bdefective\b|\bcracked\b|\bchipped\b"
    r"|\bsmashed\b|\bscratched\b|\bnot working\b|\bdoesn't work\b|\bdoes not work\b"
    r"|\barrived (?:damaged|broken|faulty)\b|\bmissing parts?\b",
    re.IGNORECASE,
)
_CHANGE_OF_MIND_HINTS = re.compile(
    r"\bchanged my mind\b|\bno longer (?:need|want)\b|\bdon't want it\b"
    r"|\bdo not want it\b|\bnot as (?:expected|described)\b|\bwrong (?:colour|color|size)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DamageEvidence:
    """Whether the email claims damage/fault and whether an image is attached."""

    damaged_or_faulty: bool
    has_image: bool
    phrases: tuple[str, ...] = ()
    change_of_mind: bool = False

    @property
    def photo_required(self) -> bool:
        """RP-3.2 2.6: the 45-day exception needs damage plus a photo."""
        return self.damaged_or_faulty

    @property
    def exception_available(self) -> bool:
        return self.damaged_or_faulty and self.has_image


def assess_damage(text: str, attachments: Sequence[Attachment] = ()) -> DamageEvidence:
    """Extract damage/fault claims and detect an image attachment (2.6).

    A supplied image is sufficient; the image content is not analysed.
    """
    phrases = tuple(
        dict.fromkeys(match.group(0).strip() for match in _DAMAGE_HINTS.finditer(text))
    )
    has_image = any(
        attachment.content_type.strip().lower().startswith("image/")
        or attachment.file_name.strip().lower().endswith(IMAGE_EXTENSIONS)
        for attachment in attachments
    )
    return DamageEvidence(
        damaged_or_faulty=bool(phrases),
        has_image=has_image,
        phrases=phrases,
        change_of_mind=bool(_CHANGE_OF_MIND_HINTS.search(text)),
    )


class WindowState(StrEnum):
    """Result of the delivery-window gate."""

    WITHIN = "WITHIN"
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    UNABLE_TO_VERIFY = "UNABLE_TO_VERIFY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class WindowEvaluation:
    """Days since carrier delivery compared with the applicable limit."""

    state: WindowState
    days_since_delivery: int | None
    allowed_days: int
    damage_exception: bool = False
    reason_code: ReasonCode | None = None
    evidence: tuple[Evidence, ...] = ()
    policy_clause: str = "2.1"

    @property
    def within_window(self) -> bool:
        return self.state in (WindowState.WITHIN, WindowState.NOT_APPLICABLE)


def evaluate_window(
    *,
    delivered_at: datetime | None,
    received_at: datetime,
    damage: DamageEvidence,
    settings: Settings | None = None,
    applies: bool = True,
) -> WindowEvaluation:
    """Evaluate the refund/return window from the carrier's delivery date.

    Uses ``shipments.delivered_at`` (never ``orders.order_date``). Damaged or
    faulty items with a photo may be requested up to 45 days after delivery;
    every other request gets 30 days (RP-3.2 2.1, 2.3, 2.6).
    """
    config = settings or Settings()

    if not applies:
        return WindowEvaluation(
            state=WindowState.NOT_APPLICABLE,
            days_since_delivery=None,
            allowed_days=config.return_window_days,
            evidence=(
                Evidence(
                    source="policy:RP-3.2",
                    ref="2.1",
                    value="window not evaluated for this ticket type",
                ),
            ),
            policy_clause="2.1",
        )

    if delivered_at is None:
        return WindowEvaluation(
            state=WindowState.UNABLE_TO_VERIFY,
            days_since_delivery=None,
            allowed_days=config.return_window_days,
            evidence=(
                Evidence(
                    source="shipments.csv",
                    ref="delivered_at",
                    value="no delivery scan; window cannot be computed",
                ),
            ),
            policy_clause="2.1",
        )

    moment = _as_utc(received_at)
    delivered = _as_utc(delivered_at)
    days = max((moment - delivered).days, 0)

    exception_eligible = damage.exception_available
    allowed_days = (
        config.damage_window_days if exception_eligible else config.return_window_days
    )
    within = days <= allowed_days
    damage_exception = exception_eligible and days > config.return_window_days

    if not within:
        state = WindowState.OUT_OF_WINDOW
        reason = ReasonCode.OUT_OF_WINDOW
        clause = "2.3"
    elif damage_exception:
        state = WindowState.WITHIN
        reason = ReasonCode.DAMAGE_EXCEPTION
        clause = "2.6"
    else:
        state = WindowState.WITHIN
        reason = None
        clause = "2.1"

    evidence = [
        Evidence(source="shipments.csv", ref="delivered_at", value=delivered.isoformat()),
        Evidence(source="email:received_at", ref="received_at", value=moment.isoformat()),
        Evidence(
            source="policy:RP-3.2",
            ref=clause,
            value=f"{days} days since delivery; allowed {allowed_days} days",
        ),
    ]
    if damage.damaged_or_faulty:
        evidence.append(
            Evidence(
                source="email:body",
                ref="damage-claim",
                value=", ".join(damage.phrases) or "damage claimed",
            )
        )
    evidence.append(
        Evidence(
            source="email:attachments",
            ref="image",
            value="present" if damage.has_image else "absent",
        )
    )

    return WindowEvaluation(
        state=state,
        days_since_delivery=days,
        allowed_days=allowed_days,
        damage_exception=damage_exception,
        reason_code=reason,
        evidence=tuple(evidence),
        policy_clause=clause,
    )


# ---------------------------------------------------------------------------
# 6. Checks (fixed order, evidence attached to every check)
# ---------------------------------------------------------------------------


def _check(
    check_id: str,
    result: CheckResult,
    clause: str,
    evidence: Sequence[Evidence],
) -> Check:
    return Check(
        check_id=check_id,
        result=result,
        evidence=list(evidence),
        policy_clause=clause,
    )


def build_checks(
    *,
    override_phrase: str | None,
    identity: IdentityResolution,
    carrier: CarrierEvaluation,
    window: WindowEvaluation,
    damage: DamageEvidence,
) -> list[Check]:
    """Emit every check in the required order with source/ref/value evidence."""
    checks: list[Check] = []

    checks.append(
        _check(
            "POLICY_INTEGRITY",
            "FAIL" if override_phrase else "PASS",
            "5.2",
            (
                Evidence(
                    source="email:body",
                    ref="policy-override",
                    value=override_phrase or "none",
                ),
            ),
        )
    )

    order_evidence = (
        Evidence(
            source="orders.csv",
            ref=identity.canonical_order_id or "(none)",
            value=f"owner={identity.order.customer_id}; status={identity.order.status}"
            if identity.order is not None
            else f"not found ({identity.status.value})",
        ),
    )
    checks.append(
        _check(
            "ORDER_IDENTIFIED",
            "PASS" if identity.order_identified else "FAIL",
            "4.1",
            order_evidence,
        )
    )

    if not identity.order_identified:
        customer_result: CheckResult = "NOT_APPLICABLE"
    elif identity.customer is None:
        customer_result = "UNABLE_TO_VERIFY"
    else:
        customer_result = "PASS" if identity.owner_verified else "FAIL"

    checks.append(
        _check(
            "CUSTOMER_MATCH",
            customer_result,
            "4.1",
            (
                Evidence(
                    source="customers.csv",
                    ref=identity.customer.customer_id if identity.customer else "(none)",
                    value=(
                        f"order owner {identity.order.customer_id}"
                        if identity.order is not None
                        else "order not identified"
                    ),
                ),
            ),
        )
    )

    checks.append(
        _check(
            "CARRIER_STATUS",
            "PASS" if carrier.status_verified else "UNABLE_TO_VERIFY",
            "4.2",
            tuple(
                evidence
                for evidence in carrier.evidence
                if evidence.source == "shipments.csv"
            )
            or (
                Evidence(
                    source="shipments.csv",
                    ref="(none)",
                    value="no carrier status",
                ),
            ),
        )
    )

    if carrier.is_delivered:
        movement_result: CheckResult = "NOT_APPLICABLE"
        movement_value = "delivered; movement not evaluated"
        movement_clause = "5.3"
    elif carrier.is_lost:
        movement_result = "FAIL"
        movement_value = (
            f"no carrier movement for {carrier.days_since_last_scan} days (> 10)"
        )
        movement_clause = "5.1"
    elif carrier.state is CarrierState.MOVING:
        movement_result = "PASS"
        movement_value = f"moving; last scan {carrier.days_since_last_scan} days ago"
        movement_clause = "5.3"
    else:
        movement_result = "UNABLE_TO_VERIFY"
        movement_value = "carrier status unavailable; movement cannot be assessed"
        movement_clause = "5.1"

    checks.append(
        _check(
            "CARRIER_MOVEMENT",
            movement_result,
            movement_clause,
            (
                Evidence(
                    source="shipments.csv",
                    ref="last_event_at",
                    value=movement_value,
                ),
                *(
                    evidence
                    for evidence in carrier.evidence
                    if evidence.source == "policy:RP-3.2"
                ),
            ),
        )
    )

    window_result: CheckResult = {
        WindowState.WITHIN: "PASS",
        WindowState.OUT_OF_WINDOW: "FAIL",
        WindowState.UNABLE_TO_VERIFY: "UNABLE_TO_VERIFY",
        WindowState.NOT_APPLICABLE: "NOT_APPLICABLE",
    }[window.state]
    checks.append(
        _check("DELIVERY_WINDOW", window_result, window.policy_clause, window.evidence)
    )

    if not damage.damaged_or_faulty:
        damage_result: CheckResult = "NOT_APPLICABLE"
        damage_value = "no damage or fault claimed"
    elif damage.has_image:
        damage_result = "PASS"
        damage_value = "damage claimed with image attachment"
    else:
        damage_result = "FAIL"
        damage_value = "damage claimed but no image attachment"

    checks.append(
        _check(
            "DAMAGE_EVIDENCE",
            damage_result,
            "2.6",
            (
                Evidence(
                    source="email:body",
                    ref="damage-claim",
                    value=", ".join(damage.phrases) or damage_value,
                ),
                Evidence(
                    source="email:attachments",
                    ref="image",
                    value="present" if damage.has_image else "absent",
                ),
            ),
        )
    )

    return checks


# ---------------------------------------------------------------------------
# 7. Money, scope and authority (gates 5-9, RP-3.2 2.2, 2.4, 2.5, 3.1-3.3)
# ---------------------------------------------------------------------------

_QUANTITY_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "both": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_QUANTITY_TOKEN = r"(?:\d{1,3}|one|two|both|three|four|five|six|seven|eight|nine|ten)"

_WHOLE_ORDER_RE = re.compile(
    r"\b(?:whole|entire|full|complete|total)\s+order\b"
    r"|\beverything\b"
    r"|\ball (?:of )?(?:the )?(?:items|order|goods)\b"
    r"|\ball of it\b",
    re.IGNORECASE,
)
_SHIPPING_RE = re.compile(
    r"\bshipping\b|\bpostage\b|\bdelivery (?:fee|charge|cost)\b|\bshipping (?:fee|cost|charge)s?\b",
    re.IGNORECASE,
)
_EXPLICIT_AMOUNT_RE = re.compile(r"(?:\$|usd\s*)(\d[\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)

_PRODUCT_STOPWORDS = frozenset(
    {"set", "pair", "with", "and", "the", "for", "pack", "of", "x"}
)


@dataclass(frozen=True, slots=True)
class LineAssessment:
    """One affected order line: affected quantity and refundable value."""

    order_id: str
    line_no: int
    product_name: str
    ordered_qty: int
    affected_qty: int
    unit_price: Decimal
    gross: Decimal
    prior_refunded: Decimal
    eligible: Decimal
    matched_term: str | None = None

    @property
    def fully_refunded(self) -> bool:
        return self.eligible == Decimal("0.00") and self.prior_refunded > Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Decision:
    """The policy decision for one ticket."""

    outcome: Outcome
    reason_codes: tuple[ReasonCode, ...]
    checks: tuple[Check, ...]
    amount: Decimal
    approver_role: ApproverRole
    human_approval_required: bool


def _product_keywords(product_name: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", product_name.lower())
    return tuple(
        word for word in words if len(word) >= 3 and word not in _PRODUCT_STOPWORDS
    )


def _matched_term(body_lower: str, product_name: str) -> str | None:
    for keyword in _product_keywords(product_name):
        pattern = re.compile(rf"\b{re.escape(keyword)}s?\b")
        match = pattern.search(body_lower)
        if match is not None:
            return keyword
    return None


def _to_quantity(token: str) -> int | None:
    cleaned = token.strip().lower()
    if cleaned.isdigit():
        return int(cleaned)
    return _QUANTITY_WORDS.get(cleaned)


def _requested_quantity(
    body_lower: str, keyword: str, ordered_qty: int
) -> int:
    """Infer the affected quantity near a product mention, capped by the order."""
    singular = re.escape(keyword)
    patterns = (
        rf"\b(?P<qty>{_QUANTITY_TOKEN})\s+of\s+(?:the\s+|my\s+|your\s+)?"
        rf"(?:{_QUANTITY_TOKEN}\s+)?{singular}s?\b",
        rf"\b(?P<qty>{_QUANTITY_TOKEN})\s+{singular}s?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, body_lower)
        if match is None:
            continue
        quantity = _to_quantity(match.group("qty"))
        if quantity is not None and quantity > 0:
            return min(quantity, ordered_qty)
    return min(1, ordered_qty)


def select_affected_lines(
    *,
    order: OrderRecord,
    lines: Sequence[OrderLineRecord],
    refunds: Sequence[RefundRecord],
    body: str,
) -> tuple[LineAssessment, ...]:
    """Select the affected lines and quantities the customer actually described.

    Only lines that exist on this order can be affected. Product names in the
    email pick the lines; a single-line order is used as-is. Quantities are
    capped at the ordered quantity (RP-3.2 2.2, 2.4).
    """
    order_lines = [line for line in lines if line.order_id == order.order_id]
    if not order_lines:
        return ()

    body_lower = body.lower()
    whole_order = _WHOLE_ORDER_RE.search(body_lower) is not None

    matched: list[tuple[OrderLineRecord, str]] = []
    for line in order_lines:
        term = _matched_term(body_lower, line.product_name)
        if term is not None:
            matched.append((line, term))

    if not matched:
        matched = [(line, None) for line in order_lines]

    issued_by_line: dict[int, Decimal] = {}
    for refund in refunds:
        if refund.order_id != order.order_id or refund.status != "ISSUED":
            continue
        issued_by_line[refund.line_no] = issued_by_line.get(
            refund.line_no, Decimal("0.00")
        ) + refund.amount

    assessments: list[LineAssessment] = []
    for line, term in matched:
        if whole_order:
            affected_qty = line.qty
        elif term is not None:
            affected_qty = _requested_quantity(body_lower, term, line.qty)
        else:
            affected_qty = min(1, line.qty)

        gross = quantize_money(Decimal(affected_qty) * line.unit_price)
        prior = quantize_money(issued_by_line.get(line.line_no, Decimal("0.00")))
        eligible = quantize_money(max(gross - min(prior, gross), Decimal("0.00")))

        assessments.append(
            LineAssessment(
                order_id=order.order_id,
                line_no=line.line_no,
                product_name=line.product_name,
                ordered_qty=line.qty,
                affected_qty=affected_qty,
                unit_price=line.unit_price,
                gross=gross,
                prior_refunded=prior,
                eligible=eligible,
                matched_term=term,
            )
        )

    return tuple(assessments)


def _asked_beyond_eligible(
    *,
    body: str,
    order: OrderRecord,
    affected: Sequence[LineAssessment],
    amount: Decimal,
    line_count: int,
) -> str | None:
    """Return the phrase showing the customer asked for more than is eligible."""
    whole_match = _WHOLE_ORDER_RE.search(body)
    affects_everything = (
        len(affected) == line_count
        and all(item.affected_qty == item.ordered_qty for item in affected)
    )

    if whole_match is not None and not affects_everything:
        return whole_match.group(0)

    shipping_match = _SHIPPING_RE.search(body)
    if shipping_match is not None and order.shipping_fee > Decimal("0.00"):
        return shipping_match.group(0)

    explicit = _EXPLICIT_AMOUNT_RE.search(body)
    if explicit is not None:
        requested = quantize_money(Decimal(explicit.group(1).replace(",", "")))
        if requested > amount:
            return explicit.group(0)

    return None


def _money_checks(
    *,
    order: OrderRecord,
    affected: Sequence[LineAssessment],
    amount: Decimal,
    asked_beyond: str | None,
    approval_required: bool,
    limit: Decimal,
    fully_refunded: bool,
    prior_refunds: Sequence[RefundRecord],
) -> list[Check]:
    scope_evidence: list[Evidence] = [
        Evidence(
            source="order_items.csv",
            ref=f"{item.order_id}#{item.line_no}",
            value=(
                f"{item.product_name}: affected {item.affected_qty} of {item.ordered_qty}"
                f" @ {item.unit_price} = {item.gross}"
            ),
        )
        for item in affected
    ]
    if asked_beyond is not None:
        scope_evidence.append(
            Evidence(source="email:body", ref="scope-request", value=asked_beyond)
        )
    if order.shipping_fee > Decimal("0.00"):
        scope_evidence.append(
            Evidence(
                source="orders.csv",
                ref=order.order_id,
                value=f"shipping {order.shipping_fee} is not refundable",
            )
        )

    checks: list[Check] = [
        _check(
            "ITEM_SCOPE",
            "PASS" if affected else "FAIL",
            "2.4" if asked_beyond is not None else "2.2",
            scope_evidence,
        )
    ]

    prior_evidence: list[Evidence] = [
        Evidence(
            source="refunds.csv",
            ref=refund.refund_id,
            value=f"line {refund.line_no}: {refund.amount} {refund.status}",
        )
        for refund in prior_refunds
        if refund.status == "ISSUED"
    ]
    prior_evidence.append(
        Evidence(
            source="refunds.csv",
            ref=order.order_id,
            value=f"remaining eligible value {amount}",
        )
    )
    checks.append(
        _check(
            "PRIOR_REFUNDS",
            "NOT_APPLICABLE"
            if not prior_evidence[:-1]
            else ("FAIL" if fully_refunded else "PASS"),
            "2.5",
            prior_evidence,
        )
    )

    checks.append(
        _check(
            "AUTHORITY",
            "FAIL" if approval_required else "PASS",
            "3.2" if approval_required else "3.1",
            (
                Evidence(
                    source="policy:RP-3.2",
                    ref="3.1-3.2",
                    value=f"amount {amount} vs threshold {limit}",
                ),
                Evidence(
                    source="decision",
                    ref="approval",
                    value="SUPPORT_LEAD required"
                    if approval_required
                    else "no approval required",
                ),
            ),
        )
    )

    return checks


def assess_refund(
    *,
    order: OrderRecord,
    lines: Sequence[OrderLineRecord],
    refunds: Sequence[RefundRecord],
    body: str,
    base_checks: Sequence[Check] = (),
    settings: Settings | None = None,
) -> Decision:
    """Compute affected-line money and authority, returning the full decision.

    The amount is always the eligible unpaid value of the affected lines only:
    prior ISSUED refunds are deducted per line, shipping is never refunded, and
    a request for more than that is reduced rather than honoured (RP-3.2 2.2,
    2.4, 2.5). Amounts above the auto-approval limit are pended for a Support
    Lead (3.1, 3.2); exactly at the limit still proceeds.
    """
    config = settings or Settings()
    limit = config.refund_auto_approve_limit_usd

    affected = select_affected_lines(
        order=order, lines=lines, refunds=refunds, body=body
    )
    amount = quantize_money(
        sum((item.eligible for item in affected), Decimal("0.00"))
    )

    prior_refunds = tuple(
        refund
        for refund in refunds
        if refund.status == "ISSUED"
        and refund.order_id == order.order_id
        and any(refund.line_no == item.line_no for item in affected)
    )
    fully_refunded = bool(affected) and amount == Decimal("0.00") and bool(prior_refunds)

    order_line_count = sum(1 for line in lines if line.order_id == order.order_id)
    asked_beyond = _asked_beyond_eligible(
        body=body,
        order=order,
        affected=affected,
        amount=amount,
        line_count=order_line_count,
    )

    approval_required = amount > limit

    reasons: list[ReasonCode] = []
    if fully_refunded:
        outcome: Outcome = "REJECT"
        reasons.append(ReasonCode.ALREADY_REFUNDED)
    else:
        if prior_refunds:
            reasons.append(ReasonCode.ALREADY_REFUNDED)
        if asked_beyond is not None:
            reasons.append(ReasonCode.PARTIAL_LINES)
        if approval_required:
            reasons.append(ReasonCode.APPROVAL_REQUIRED)
            outcome = "PEND"
        elif asked_beyond is not None:
            outcome = "PROCEED_MODIFIED"
        else:
            outcome = "PROCEED"

    money_checks = _money_checks(
        order=order,
        affected=affected,
        amount=amount,
        asked_beyond=asked_beyond,
        approval_required=approval_required,
        limit=limit,
        fully_refunded=fully_refunded,
        prior_refunds=prior_refunds,
    )

    checks = _ordered_checks([*base_checks, *money_checks])

    return Decision(
        outcome=outcome,
        reason_codes=tuple(dict.fromkeys(reasons)),
        checks=checks,
        amount=amount,
        approver_role="SUPPORT_LEAD" if approval_required else None,
        human_approval_required=approval_required,
    )


def _ordered_checks(checks: Sequence[Check]) -> tuple[Check, ...]:
    """Return checks in the mandated order, rejecting unknown check ids."""
    known = {check_id: index for index, check_id in enumerate(CHECK_ORDER)}
    for check in checks:
        if check.check_id not in known:
            raise ValueError(f"unknown check_id: {check.check_id}")
    return tuple(sorted(checks, key=lambda check: known[check.check_id]))


def adjudicate_ticket(
    dataset: Dataset,
    ticket: Ticket,
    *,
    settings: Settings | None = None,
) -> Decision:
    """Run the ordered gates for one ticket and return the decision.

    The order is fixed: safety, identity, carrier, window, then the refund
    money/authority gates. Non-refund ticket types stop at a status answer.
    """
    config = settings or Settings()
    text = f"{ticket.subject}\n{ticket.body}"
    received_at = parse_timestamp(ticket.received_at)
    if received_at is None:
        raise ValueError(f"ticket {ticket.ticket_id} has no usable received_at")

    override = detect_policy_override(text)
    identity = resolve_identity(
        dataset, email=ticket.from_, order_reference=normalise_order_id(text)
    )
    ticket_type = classify_ticket(subject=ticket.subject, body=ticket.body)
    damage = assess_damage(text, ticket.attachments)

    order = identity.order
    shipment = (
        dataset.shipment_for_order(order.order_id) if order is not None else None
    )
    carrier = evaluate_carrier(shipment, now=received_at, settings=config)
    window = evaluate_window(
        delivered_at=carrier.delivered_at,
        received_at=received_at,
        damage=damage,
        settings=config,
        applies=ticket_type in {"REFUND", "RETURN"},
    )
    checks = _ordered_checks(
        build_checks(
            override_phrase=override,
            identity=identity,
            carrier=carrier,
            window=window,
            damage=damage,
        )
    )
    zero = Decimal("0.00")

    if override is not None:
        return Decision(
            outcome="ESCALATE",
            reason_codes=(ReasonCode.POLICY_OVERRIDE_ATTEMPT,),
            checks=checks,
            amount=zero,
            approver_role="TRUST_SAFETY",
            human_approval_required=True,
        )

    if not identity.order_identified or not identity.owner_verified:
        return Decision(
            outcome="INVALID_REQUEST",
            reason_codes=(
                (identity.reason_code or ReasonCode.CUSTOMER_MISMATCH),
            ),
            checks=checks,
            amount=zero,
            approver_role=None,
            human_approval_required=False,
        )

    if carrier.state is CarrierState.UNAVAILABLE:
        return Decision(
            outcome="UNABLE_TO_VERIFY",
            reason_codes=(ReasonCode.CARRIER_STATUS_UNAVAILABLE,),
            checks=checks,
            amount=zero,
            approver_role=None,
            human_approval_required=False,
        )

    if carrier.is_lost:
        return Decision(
            outcome="ESCALATE",
            reason_codes=(ReasonCode.LOST_IN_TRANSIT,),
            checks=checks,
            amount=zero,
            approver_role="CARRIER_CLAIMS",
            human_approval_required=True,
        )

    if ticket_type in {"ORDER_STATUS", "DELIVERY_ISSUE", "OTHER"}:
        return Decision(
            outcome="PROCEED",
            reason_codes=(),
            checks=checks,
            amount=zero,
            approver_role=None,
            human_approval_required=False,
        )

    if window.state is WindowState.OUT_OF_WINDOW:
        return Decision(
            outcome="REJECT",
            reason_codes=(ReasonCode.OUT_OF_WINDOW,),
            checks=checks,
            amount=zero,
            approver_role=None,
            human_approval_required=False,
        )

    if window.state is WindowState.UNABLE_TO_VERIFY:
        return Decision(
            outcome="UNABLE_TO_VERIFY",
            reason_codes=(),
            checks=checks,
            amount=zero,
            approver_role=None,
            human_approval_required=False,
        )

    assert order is not None  # identity was verified above
    decision = assess_refund(
        order=order,
        lines=dataset.order_lines(order.order_id),
        refunds=dataset.issued_refunds(order.order_id),
        body=text,
        base_checks=checks,
        settings=config,
    )

    if window.reason_code is ReasonCode.DAMAGE_EXCEPTION:
        decision = Decision(
            outcome=decision.outcome,
            reason_codes=tuple(
                dict.fromkeys((ReasonCode.DAMAGE_EXCEPTION, *decision.reason_codes))
            ),
            checks=decision.checks,
            amount=decision.amount,
            approver_role=decision.approver_role,
            human_approval_required=decision.human_approval_required,
        )

    return decision



def refundable_amount(
    lines: Sequence[OrderLineRecord], requested_qty: int
) -> Decimal:
    """Return the affected-line value for the requested, capped quantity."""
    total = Decimal("0.00")
    for line in lines:
        quantity = min(max(requested_qty, 0), line.qty)
        total += Decimal(quantity) * line.unit_price
    return quantize_money(total)

