"""Money, scope and authority gates (RP-3.2 2.2, 2.4, 2.5, 3.1-3.3)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.ingest import OrderLineRecord, OrderRecord, RefundRecord
from src.models import Check
from src.policy import (
    BASE_CHECK_ORDER,
    CHECK_ORDER,
    ReasonCode,
    assess_refund,
    refundable_amount,
    select_affected_lines,
)

AT = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
ORDER_ID = "LF-900001"
VOCABULARY = {code.value for code in ReasonCode}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _order(*, total: str = "0.00", shipping: str = "0.00") -> OrderRecord:
    return OrderRecord(
        order_id=ORDER_ID,
        customer_id="C90000",
        order_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
        status="DELIVERED",
        order_total=Decimal(total),
        shipping_fee=Decimal(shipping),
        currency="USD",
        payment_method="card",
    )


def _line(line_no: int, name: str, qty: int, price: str) -> OrderLineRecord:
    return OrderLineRecord(
        order_id=ORDER_ID,
        line_no=line_no,
        sku=f"SKU-{line_no:02d}",
        product_name=name,
        qty=qty,
        unit_price=Decimal(price),
    )


def _refund(line_no: int, amount: str, refund_id: str = "RF-9001") -> RefundRecord:
    return RefundRecord(
        refund_id=refund_id,
        order_id=ORDER_ID,
        line_no=line_no,
        amount=Decimal(amount),
        status="ISSUED",
        requested_at=AT,
        approved_by="auto",
    )


def _decision(lines, body: str, *, total="0.00", shipping="0.00", refunds=()):
    return assess_refund(
        order=_order(total=total, shipping=shipping),
        lines=lines,
        refunds=list(refunds),
        body=body,
    )


def _check(decision, check_id: str):
    return next(check for check in decision.checks if check.check_id == check_id)


# ---------------------------------------------------------------------------
# Authority boundary
# ---------------------------------------------------------------------------


def test_exactly_150_auto_approves() -> None:
    decision = _decision(
        [_line(1, "Round wall mirror", 1, "150.00")],
        "The mirror arrived damaged, please refund it.",
        total="150.00",
    )

    assert decision.amount == Decimal("150.00")
    assert decision.outcome == "PROCEED"
    assert decision.human_approval_required is False
    assert decision.approver_role is None
    assert decision.reason_codes == ()
    assert _check(decision, "AUTHORITY").result == "PASS"


def test_150_01_requires_support_lead() -> None:
    decision = _decision(
        [_line(1, "Round wall mirror", 1, "150.01")],
        "The mirror arrived damaged, please refund it.",
        total="150.01",
    )

    assert decision.amount == Decimal("150.01")
    assert decision.outcome == "PEND"
    assert decision.human_approval_required is True
    assert decision.approver_role == "SUPPORT_LEAD"
    assert decision.reason_codes == (ReasonCode.APPROVAL_REQUIRED,)
    assert _check(decision, "AUTHORITY").result == "FAIL"
    assert _check(decision, "AUTHORITY").policy_clause == "3.2"


# ---------------------------------------------------------------------------
# Scope: affected lines and quantities
# ---------------------------------------------------------------------------


def test_one_of_two_chairs_is_one_chair() -> None:
    lines = [
        _line(1, "Glass vase", 1, "45.00"),
        _line(2, "Oak dining chair", 2, "60.00"),
    ]

    decision = _decision(
        lines, "One of the two chairs arrived damaged.", total="165.00"
    )
    affected = select_affected_lines(
        order=_order(total="165.00"), lines=lines, refunds=[], body="One of the two chairs arrived damaged."
    )

    assert decision.amount == Decimal("60.00")  # one chair, not two
    assert decision.outcome == "PROCEED"
    assert len(affected) == 1
    assert affected[0].line_no == 2
    assert affected[0].affected_qty == 1
    assert affected[0].ordered_qty == 2

    scope = _check(decision, "ITEM_SCOPE")
    assert scope.result == "PASS"
    assert not any("vase" in row.value.lower() for row in scope.evidence)


def test_quantity_is_capped_at_ordered_quantity() -> None:
    decision = _decision(
        [_line(1, "Oak dining chair", 2, "60.00")],
        "All 9 chairs are broken, refund them all.",
        total="120.00",
    )
    affected = select_affected_lines(
        order=_order(total="120.00"),
        lines=[_line(1, "Oak dining chair", 2, "60.00")],
        refunds=[],
        body="All 9 chairs are broken, refund them all.",
    )

    assert affected[0].affected_qty == 2
    assert decision.amount == Decimal("120.00")


def test_whole_order_request_is_modified_to_affected_line() -> None:
    lines = [
        _line(1, "Glass vase", 1, "45.00"),
        _line(2, "Linen throw", 1, "60.00"),
    ]

    decision = _decision(
        lines,
        "The vase arrived broken. I want a refund for the whole order.",
        total="105.00",
    )

    assert decision.amount == Decimal("45.00")  # affected line only
    assert decision.outcome == "PROCEED_MODIFIED"
    assert decision.reason_codes == (ReasonCode.PARTIAL_LINES,)
    assert _check(decision, "ITEM_SCOPE").policy_clause == "2.4"
    assert any(
        row.ref == "scope-request" and "whole order" in row.value.lower()
        for row in _check(decision, "ITEM_SCOPE").evidence
    )


def test_shipping_is_never_refunded() -> None:
    decision = _decision(
        [_line(1, "Glass vase", 1, "45.00")],
        "The vase is damaged. Please refund the item and the shipping fee.",
        total="54.95",
        shipping="9.95",
    )

    assert decision.amount == Decimal("45.00")
    assert decision.amount < Decimal("54.95")
    assert decision.outcome == "PROCEED_MODIFIED"
    assert ReasonCode.PARTIAL_LINES in decision.reason_codes
    assert any(
        "shipping 9.95 is not refundable" in row.value
        for row in _check(decision, "ITEM_SCOPE").evidence
    )


def test_explicit_request_above_eligibility_is_modified() -> None:
    decision = _decision(
        [_line(1, "Round wall mirror", 1, "150.00")],
        "Refund me $400 for the mirror.",
        total="150.00",
    )

    assert decision.amount == Decimal("150.00")
    assert decision.outcome == "PROCEED_MODIFIED"
    assert decision.reason_codes == (ReasonCode.PARTIAL_LINES,)


# ---------------------------------------------------------------------------
# Prior refunds
# ---------------------------------------------------------------------------


def test_already_refunded_line_is_rejected() -> None:
    decision = _decision(
        [_line(1, "Glass vase", 1, "45.00")],
        "I want a refund for the vase.",
        total="45.00",
        refunds=[_refund(1, "45.00")],
    )

    assert decision.amount == Decimal("0.00")
    assert decision.outcome == "REJECT"
    assert decision.reason_codes == (ReasonCode.ALREADY_REFUNDED,)
    assert decision.human_approval_required is False
    assert _check(decision, "PRIOR_REFUNDS").result == "FAIL"


def test_prior_refund_is_deducted_from_a_partial_quantity() -> None:
    """Two chairs at $185 with one unit already refunded leaves one unit."""
    decision = _decision(
        [_line(1, "Oak dining chair", 2, "185.00")],
        "Both chairs are faulty, please refund them.",
        total="370.00",
        refunds=[_refund(1, "185.00")],
    )

    assert decision.amount == Decimal("185.00")
    assert decision.outcome == "PEND"
    assert _check(decision, "PRIOR_REFUNDS").result == "PASS"


def test_prior_refund_and_approval_together() -> None:
    lines = [
        _line(1, "Glass vase", 1, "45.00"),
        _line(2, "Oak dining chair", 2, "185.00"),
    ]

    decision = _decision(
        lines,
        "Please refund the vase and one of the two chairs.",
        total="415.00",
        refunds=[_refund(1, "45.00")],
    )

    assert decision.amount == Decimal("185.00")
    assert decision.outcome == "PEND"
    assert decision.reason_codes == (
        ReasonCode.ALREADY_REFUNDED,
        ReasonCode.APPROVAL_REQUIRED,
    )
    assert decision.human_approval_required is True
    assert decision.approver_role == "SUPPORT_LEAD"
    assert _check(decision, "PRIOR_REFUNDS").result == "PASS"
    assert _check(decision, "AUTHORITY").result == "FAIL"


def test_no_prior_refund_means_not_applicable() -> None:
    decision = _decision(
        [_line(1, "Glass vase", 1, "45.00")],
        "The vase arrived damaged.",
        total="45.00",
    )

    prior = _check(decision, "PRIOR_REFUNDS")
    assert prior.result == "NOT_APPLICABLE"
    assert prior.policy_clause == "2.5"


# ---------------------------------------------------------------------------
# Decision shape, ordering and vocabulary
# ---------------------------------------------------------------------------


def test_checks_are_joined_in_the_mandated_order() -> None:
    base = [
        Check(check_id=check_id, result="NOT_APPLICABLE", policy_clause="2.1")
        for check_id in BASE_CHECK_ORDER
    ]

    decision = assess_refund(
        order=_order(total="45.00"),
        lines=[_line(1, "Glass vase", 1, "45.00")],
        refunds=[],
        body="The vase arrived damaged.",
        base_checks=base,
    )

    assert [check.check_id for check in decision.checks] == list(CHECK_ORDER)


def test_money_checks_cover_the_three_required_ids() -> None:
    decision = _decision(
        [_line(1, "Glass vase", 1, "45.00")], "The vase arrived damaged.", total="45.00"
    )

    ids = [check.check_id for check in decision.checks]
    assert ids == ["ITEM_SCOPE", "PRIOR_REFUNDS", "AUTHORITY"]
    for check in decision.checks:
        assert check.evidence
        assert check.policy_clause in {"2.2", "2.4", "2.5", "3.1", "3.2"}


@pytest.mark.parametrize(
    ("body", "price", "already_refunded", "expected_outcome"),
    [
        ("The vase arrived damaged.", "45.00", False, "PROCEED"),
        ("Refund the whole order, the vase broke.", "45.00", False, "PROCEED"),
        ("Refund the vase.", "45.00", True, "REJECT"),
        ("Refund the vase.", "400.00", False, "PEND"),
    ],
)
def test_reason_codes_stay_in_policy_vocabulary(
    body: str, price: str, already_refunded: bool, expected_outcome: str
) -> None:
    refunds = [_refund(1, price)] if already_refunded else []
    decision = _decision(
        [_line(1, "Glass vase", 1, price)],
        body,
        total=price,
        refunds=refunds,
    )

    assert decision.outcome == expected_outcome
    assert {code.value for code in decision.reason_codes} <= VOCABULARY
    if expected_outcome == "PROCEED":
        assert decision.reason_codes == ()


def test_amounts_are_two_decimal_decimals() -> None:
    decision = _decision(
        [_line(1, "Oak dining chair", 2, "184.995")],
        "Both chairs are damaged.",
        total="369.99",
    )

    assert isinstance(decision.amount, Decimal)
    assert decision.amount == decision.amount.quantize(Decimal("0.01"))


def test_refundable_amount_helper_caps_quantity() -> None:
    lines = [_line(1, "Oak dining chair", 2, "60.00")]

    assert refundable_amount(lines, 1) == Decimal("60.00")
    assert refundable_amount(lines, 9) == Decimal("120.00")
    assert refundable_amount(lines, 0) == Decimal("0.00")
