"""Ingest: normalisation, duplicate collapsing, indexes and dirty data."""
from __future__ import annotations

import csv
import logging
from collections import Counter
from datetime import date, timezone
from pathlib import Path

import pytest

from src.ingest import (
    load_dataset,
    normalise_email,
    normalise_order_id,
    parse_date,
    parse_timestamp,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def dataset():
    return load_dataset(data_dir=DATA_DIR)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------------------
# Duplicate order ids
# ---------------------------------------------------------------------------


def test_both_duplicate_order_ids_are_collapsed(dataset) -> None:
    """Every repeated order_id in the export appears once in the dataset."""
    rows = _csv_rows(DATA_DIR / "orders.csv")
    counts = Counter(row["order_id"] for row in rows)
    repeated = sorted(order_id for order_id, count in counts.items() if count > 1)

    assert repeated, "fixture assumption: orders.csv contains duplicate rows"
    assert sorted(dataset.duplicate_order_ids) == repeated
    assert len(dataset.orders_by_id) == len(counts)  # one record per unique id
    assert len(rows) == len(dataset.orders_by_id) + len(repeated)


def test_duplicate_rows_were_identical(dataset) -> None:
    """A collapsed duplicate must match the surviving row field-for-field."""
    rows = _csv_rows(DATA_DIR / "orders.csv")
    by_id: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_id.setdefault(row["order_id"], []).append(row)

    for order_id in dataset.duplicate_order_ids:
        first, *rest = by_id[order_id]
        assert all(row == first for row in rest)
        record = dataset.orders_by_id[order_id]
        assert record.customer_id == first["customer_id"]
        assert record.status == first["status"]


def test_duplicate_collapse_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        loaded = load_dataset(data_dir=DATA_DIR)

    messages = [record.getMessage() for record in caplog.records]
    collapsed = [message for message in messages if "collapsed" in message]
    assert len(collapsed) == len(loaded.duplicate_order_ids)


def test_conflicting_duplicate_raises(tmp_path: Path) -> None:
    """Identical duplicates are safe to collapse; conflicting ones are not."""
    _write_minimal_pack(
        tmp_path,
        orders_rows=[
            _order_row("LF-100200", customer_id="C10000", total="210.00"),
            _order_row("LF-100200", customer_id="C10000", total="999.00"),
        ],
    )

    with pytest.raises(ValueError, match="conflicting duplicate"):
        load_dataset(data_dir=tmp_path)


def test_identical_duplicate_in_temp_pack_is_collapsed(tmp_path: Path) -> None:
    _write_minimal_pack(
        tmp_path,
        orders_rows=[
            _order_row("LF-100200", customer_id="C10000", total="210.00"),
            _order_row("LF-100200", customer_id="C10000", total="210.00"),
        ],
    )

    loaded = load_dataset(data_dir=tmp_path)

    assert loaded.duplicate_order_ids == ("LF-100200",)
    assert len(loaded.orders_by_id) == 1


# ---------------------------------------------------------------------------
# Order reference styles
# ---------------------------------------------------------------------------


def test_all_five_order_reference_styles_resolve(dataset) -> None:
    """Canonical, dashless, hashed, bare and prose references all agree."""
    canonical = next(iter(dataset.orders_by_id))
    number = canonical.split("-", 1)[1]

    styles = [
        f"LF-{number}",
        f"LF{number}",
        f"#{number}",
        f"{number}",
        f"  re: order order {number}, please help  ",
    ]

    assert {normalise_order_id(raw) for raw in styles} == {canonical}
    for raw in styles:
        order = dataset.order(raw)
        assert order is not None
        assert order.order_id == canonical


def test_reference_normalisation_is_case_insensitive() -> None:
    canonical = "LF-100200"
    assert normalise_order_id("lf-100200") == canonical
    assert normalise_order_id("Lf100200") == canonical


def test_near_miss_reference_is_rejected() -> None:
    """A seven-digit or truncated reference must never resolve to an order."""
    assert normalise_order_id("LF-1002002") is None
    assert normalise_order_id("#10020") is None
    assert normalise_order_id("order 10020001") is None
    assert normalise_order_id("") is None
    assert normalise_order_id(None) is None


def test_unknown_order_is_not_substituted(dataset) -> None:
    canonical = next(iter(dataset.orders_by_id))
    number = canonical.split("-", 1)[1]
    missing = f"LF-{int(number) + 9000}"
    assert missing not in dataset.orders_by_id
    assert dataset.order(missing) is None
    assert dataset.order_lines(missing) == ()


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


def test_normalise_email_strips_and_lowercases() -> None:
    assert normalise_email("  ASHA.Shah0@Mail.Example ") == "asha.shah0@mail.example"
    assert normalise_email("\tBen.Tanaka1@MAIL.EXAMPLE\n") == "ben.tanaka1@mail.example"
    assert normalise_email("") == ""


def test_emails_with_spaces_and_capitals_match_their_customer(dataset) -> None:
    email, customer = next(iter(dataset.customers_by_email.items()))
    messy = f"   {email.upper()}   "

    assert dataset.customer_by_email(messy) == customer
    assert dataset.customer_by_email(messy).customer_id == customer.customer_id
    assert dataset.customer_by_email("nobody@example.invalid") is None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_ambiguous_slash_date_is_flagged() -> None:
    parsed, is_ambiguous = parse_date("08/10/2026")

    assert is_ambiguous is True
    assert parsed == date(2026, 10, 8)  # day-first reading wins


def test_unambiguous_dates_are_not_flagged() -> None:
    assert parse_date("2026-10-02") == (date(2026, 10, 2), False)
    assert parse_date("26/08/2026") == (date(2026, 8, 26), False)
    assert parse_date("10/08/2026") == (date(2026, 8, 10), True)
    assert parse_date("") == (None, False)


def test_dataset_surfaces_ambiguous_dates(dataset) -> None:
    order_dates = [item for item in dataset.ambiguous_dates if item.source == "orders.csv:order_date"]

    assert order_dates, "orders.csv mixes slash dates that are genuinely ambiguous"
    for item in order_dates:
        assert item.resolved != item.alternative
        assert parse_date(item.raw) == (item.resolved, True)

    raw_values = {item.raw for item in order_dates}
    csv_values = {row["order_date"] for row in _csv_rows(DATA_DIR / "orders.csv")}
    assert raw_values <= csv_values


def test_timestamps_are_timezone_aware(dataset) -> None:
    for shipment in dataset.shipments_by_order.values():
        for value in (shipment.shipped_at, shipment.delivered_at, shipment.last_event_at):
            if value is not None:
                assert value.tzinfo is not None
                assert value.utcoffset() is not None
                assert value.utcoffset().total_seconds() == 0
                assert value.tzinfo is timezone.utc or value.utcoffset() == timezone.utc.utcoffset(None)

    assert parse_timestamp("") is None
    assert parse_timestamp("2026-10-06T17:00").tzinfo is not None


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_indexes_are_consistent(dataset) -> None:
    assert len(dataset.customers_by_id) == len(set(dataset.customers_by_id))
    assert len(dataset.orders_by_id) == len(set(dataset.orders_by_id))
    assert set(dataset.items_by_order) == set(dataset.orders_by_id)

    for order_id, lines in dataset.items_by_order.items():
        assert [line.line_no for line in lines] == sorted(line.line_no for line in lines)
        for line in lines:
            assert line.order_id == order_id
            assert line.qty >= 1

    for order_id, shipment in dataset.shipments_by_order.items():
        assert shipment.order_id == order_id
        assert shipment.status in {"DELIVERED", "IN_TRANSIT", "UNAVAILABLE"}

    for (order_id, line_no), refunds in dataset.refunds_by_order_line.items():
        assert dataset.order(order_id) is not None
        for refund in refunds:
            assert refund.order_id == order_id
            assert refund.line_no == line_no
            assert refund.status == "ISSUED"


def test_indexes_cover_known_dirty_data(dataset) -> None:
    """UNAVAILABLE shipments and multi-line orders survive loading intact."""
    assert any(shipment.status == "UNAVAILABLE" for shipment in dataset.shipments_by_order.values())
    assert any(len(lines) > 1 for lines in dataset.items_by_order.values())

    undelivered = [
        shipment
        for shipment in dataset.shipments_by_order.values()
        if shipment.status != "DELIVERED"
    ]
    assert undelivered
    assert all(shipment.delivered_at is None for shipment in undelivered)

    order_refunds = dataset.issued_refunds(next(iter(dataset.orders_by_id)))
    assert isinstance(order_refunds, tuple)


def test_lookups_are_normalising(dataset) -> None:
    canonical = next(iter(dataset.orders_by_id))
    order = dataset.order(canonical)
    assert order is not None
    assert dataset.customer(order.customer_id) is not None
    assert dataset.shipment_for_order(canonical) is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _order_row(
    order_id: str, *, customer_id: str = "C10000", total: str = "210.00"
) -> dict[str, str]:
    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "order_date": "2026-08-18",
        "status": "DELIVERED",
        "Order Total": total,
        "shipping_fee": "0.00",
        "currency": "USD",
        "payment_method": "card",
    }


def _write_minimal_pack(tmp_path: Path, *, orders_rows: list[dict[str, str]]) -> None:
    (tmp_path / "customers.csv").write_text(
        "customer_id,name,email,country,created_at,loyalty_tier\n"
        "C10000,Asha Shah,asha.shah0@mail.example,US,2025-09-06,silver\n",
        encoding="utf-8",
    )

    with (tmp_path / "orders.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "order_id",
                "customer_id",
                "order_date",
                "status",
                "Order Total",
                "shipping_fee",
                "currency",
                "payment_method",
            ],
        )
        writer.writeheader()
        writer.writerows(orders_rows)

    (tmp_path / "order_items.csv").write_text(
        "order_id,line_no,sku,product_name,qty,unit_price\n", encoding="utf-8"
    )
    (tmp_path / "shipments.csv").write_text(
        "shipment_id,order_id,carrier,tracking_no,status,shipped_at,delivered_at,last_event_at\n",
        encoding="utf-8",
    )
    (tmp_path / "refunds.csv").write_text(
        "refund_id,order_id,line_no,amount,status,requested_at,approved_by\n",
        encoding="utf-8",
    )
