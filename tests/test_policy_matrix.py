"""Deterministic policy matrix: scope, windows, carrier, ids, money, authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.ingest import (
    OrderLineRecord,
    OrderRecord,
    ShipmentRecord,
    normalise_order_id,
)
from src.models import Attachment, Ticket
from src.policy import (
    CarrierState,
    ReasonCode,
    WindowState,
    assess_damage,
    assess_refund,
    evaluate_carrier,
    evaluate_window,
    select_affected_lines,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
IMAGE = Attachment(
    file_name="photo.jpg", content_type="image/jpeg", content_base64="aGVsbG8="
)


# ---------------------------------------------------------------------------
# Scope: one of two chairs, shipping, prior refunds
# ---------------------------------------------------------------------------


def _two_line_order(dataset) -> str:
    for order_id, lines in dataset.items_by_order.items():
        if any(line.qty >= 2 for line in lines) and order_id in {
            refund.order_id
            for refunds in dataset.refunds_by_order_line.values()
            for refund in refunds
        }:
            return order_id
    raise AssertionError("fixture data no longer has a refunded multi-line order")


def test_one_of_two_chairs_refunds_only_one_chair(dataset) -> None:
    order_id = _two_line_order(dataset)
    order = dataset.orders_by_id[order_id]
    lines = dataset.order_lines(order_id)
    body = "One of the two oak dining chairs arrived damaged."

    affected = select_affected_lines(
        order=order, lines=lines, refunds=dataset.issued_refunds(order_id), body=body
    )
    decision = assess_refund(
        order=order,
        lines=lines,
        refunds=dataset.issued_refunds(order_id),
        body=body,
    )

    chair = [item for item in affected if item.ordered_qty >= 2]
    assert len(chair) == 1
    assert chair[0].affected_qty == 1
    assert chair[0].eligible == chair[0].unit_price
    assert decision.amount == chair[0].unit_price


def test_prior_refund_plus_approval_pends_with_both_codes(dataset) -> None:
    order_id = _two_line_order(dataset)
    order = dataset.orders_by_id[order_id]
    lines = dataset.order_lines(order_id)
    body = "Please refund the vase and one of the two chairs."

    decision = assess_refund(
        order=order,
        lines=lines,
        refunds=dataset.issued_refunds(order_id),
        body=body,
    )

    assert decision.outcome == "PEND"
    assert decision.human_approval_required is True
    assert decision.approver_role == "SUPPORT_LEAD"
    assert set(decision.reason_codes) == {
        ReasonCode.ALREADY_REFUNDED,
        ReasonCode.APPROVAL_REQUIRED,
    }
    assert decision.amount > Decimal("150.00")


def test_shipping_is_never_refunded() -> None:
    order = _order(shipping="9.95")
    decision = assess_refund(
        order=order,
        lines=[_line("Glass vase", 1, "45.00")],
        refunds=[],
        body="The vase arrived damaged. Please refund the item and the shipping fee.",
    )

    assert decision.amount == Decimal("45.00")
    assert ReasonCode.PARTIAL_LINES in decision.reason_codes
    assert any(
        "shipping 9.95 is not refundable" in row.value
        for check in decision.checks
        if check.check_id == "ITEM_SCOPE"
        for row in check.evidence
    )


# ---------------------------------------------------------------------------
# Windows: 40-day damage photo, 40-day fault without photo, 45-day change of mind
# ---------------------------------------------------------------------------


def test_40_day_damage_with_photo_uses_45_day_exception() -> None:
    delivered = NOW - timedelta(days=40)
    damage = assess_damage("The lamp arrived damaged.", [IMAGE])

    evaluation = evaluate_window(
        delivered_at=delivered, received_at=NOW, damage=damage
    )

    assert evaluation.state is WindowState.WITHIN
    assert evaluation.allowed_days == 45
    assert evaluation.reason_code is ReasonCode.DAMAGE_EXCEPTION


def test_40_day_fault_without_photo_is_out_of_window() -> None:
    delivered = NOW - timedelta(days=40)
    damage = assess_damage("The lamp arrived faulty, please refund it.")

    evaluation = evaluate_window(
        delivered_at=delivered, received_at=NOW, damage=damage
    )

    assert damage.has_image is False
    assert evaluation.state is WindowState.OUT_OF_WINDOW
    assert evaluation.allowed_days == 30
    assert evaluation.reason_code is ReasonCode.OUT_OF_WINDOW


def test_45_day_change_of_mind_is_out_of_window_even_with_photo() -> None:
    delivered = NOW - timedelta(days=45)
    damage = assess_damage("I changed my mind and no longer need it.", [IMAGE])

    evaluation = evaluate_window(
        delivered_at=delivered, received_at=NOW, damage=damage
    )

    assert damage.damaged_or_faulty is False
    assert evaluation.state is WindowState.OUT_OF_WINDOW
    assert evaluation.reason_code is ReasonCode.OUT_OF_WINDOW


# ---------------------------------------------------------------------------
# Carrier: unavailable, and the 10-day boundary
# ---------------------------------------------------------------------------


def test_carrier_unavailable_cannot_be_verified(dataset) -> None:
    shipment = next(
        item
        for item in dataset.shipments_by_order.values()
        if item.status == "UNAVAILABLE"
    )

    evaluation = evaluate_carrier(shipment, now=NOW)

    assert evaluation.state is CarrierState.UNAVAILABLE
    assert evaluation.reason_code is ReasonCode.CARRIER_STATUS_UNAVAILABLE
    assert evaluation.status_verified is False


def _shipment(last_event: datetime) -> ShipmentRecord:
    return ShipmentRecord(
        shipment_id="SHP-BOUNDARY",
        order_id="LF-000000",
        carrier="TestCarrier",
        tracking_no="TRK000000000",
        status="IN_TRANSIT",
        shipped_at=last_event - timedelta(days=5),
        delivered_at=None,
        last_event_at=last_event,
    )


def test_exactly_ten_days_without_scan_is_still_moving() -> None:
    evaluation = evaluate_carrier(_shipment(NOW - timedelta(days=10)), now=NOW)

    assert evaluation.state is CarrierState.MOVING
    assert evaluation.reason_code is None


def test_ten_days_and_two_hours_without_scan_is_lost() -> None:
    last_event = NOW - timedelta(days=10, hours=2)

    evaluation = evaluate_carrier(_shipment(last_event), now=NOW)

    assert evaluation.state is CarrierState.LOST
    assert evaluation.reason_code is ReasonCode.LOST_IN_TRANSIT


# ---------------------------------------------------------------------------
# Order references
# ---------------------------------------------------------------------------


def test_all_five_order_reference_formats_resolve(dataset) -> None:
    canonical = next(iter(dataset.orders_by_id))
    number = canonical.split("-", 1)[1]
    styles = [
        canonical,
        f"LF{number}",
        f"#{number}",
        number,
        f"order order {number}",
    ]

    assert {normalise_order_id(style) for style in styles} == {canonical}
    for style in styles:
        assert dataset.order(style).order_id == canonical


def test_unknown_order_never_matches_a_nearby_order(dataset, inbox_rows) -> None:
    base = next(row for row in inbox_rows if row["ticket_id"] == "T002")
    unknown = "LF-109777"

    assert dataset.order(unknown) is None
    assert normalise_order_id("LF-1002002") is None
    assert normalise_order_id("LF-100200") == "LF-100200"

    from src.policy import adjudicate_ticket

    ticket = Ticket.model_validate(
        {**base, "ticket_id": "T-UNKNOWN", "body": "My order LF-109777 arrived broken."}
    )
    decision = adjudicate_ticket(dataset, ticket)

    assert decision.outcome == "INVALID_REQUEST"
    assert decision.reason_codes == (ReasonCode.ORDER_NOT_FOUND,)
    assert decision.amount == 0


# ---------------------------------------------------------------------------
# Authority boundary
# ---------------------------------------------------------------------------


def test_150_00_proceeds_and_150_01_pends() -> None:
    at_limit = assess_refund(
        order=_order(),
        lines=[_line("Round wall mirror", 1, "150.00")],
        refunds=[],
        body="The mirror arrived damaged.",
    )
    over_limit = assess_refund(
        order=_order(),
        lines=[_line("Round wall mirror", 1, "150.01")],
        refunds=[],
        body="The mirror arrived damaged.",
    )

    assert at_limit.outcome == "PROCEED"
    assert at_limit.human_approval_required is False
    assert at_limit.amount == Decimal("150.00")

    assert over_limit.outcome == "PEND"
    assert over_limit.human_approval_required is True
    assert over_limit.approver_role == "SUPPORT_LEAD"
    assert over_limit.reason_codes == (ReasonCode.APPROVAL_REQUIRED,)
    assert over_limit.amount == Decimal("150.01")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _order(*, shipping: str = "0.00") -> OrderRecord:
    return OrderRecord(
        order_id="LF-900001",
        customer_id="C90000",
        order_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
        status="DELIVERED",
        order_total=Decimal("0.00"),
        shipping_fee=Decimal(shipping),
        currency="USD",
        payment_method="card",
    )


def _line(name: str, qty: int, price: str) -> OrderLineRecord:
    return OrderLineRecord(
        order_id="LF-900001",
        line_no=1,
        sku="SKU-01",
        product_name=name,
        qty=qty,
        unit_price=Decimal(price),
    )
