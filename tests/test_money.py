"""Money parsing: exact decimals, no binary-float arithmetic."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.ingest import load_dataset
from src.money import money_to_float, parse_money, quantize_money, sum_money, zero

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.mark.parametrize(
    "raw",
    [
        "1234.56",
        "$1,234.56",
        "USD 1234.56",
        "USD1,234.56",
        "  $ 1,234.56  ",
        "1,234.56",
    ],
)
def test_identity_of_money_formats(raw: str) -> None:
    """The three source formats (plain, dollar, USD-prefixed) agree exactly."""
    assert parse_money(raw) == Decimal("1234.56")


def test_three_named_formats_are_identical() -> None:
    """Explicitly compare the three formats the brief calls out."""
    plain = parse_money("210.00")
    dollar = parse_money("$210.00")
    coded = parse_money("USD 210.00")

    assert plain == dollar == coded == Decimal("210.00")
    assert isinstance(plain, Decimal)


def test_thousands_separator() -> None:
    assert parse_money("1,234.56") == Decimal("1234.56")
    assert parse_money("$12,345.67") == Decimal("12345.67")


def test_result_is_quantized_to_cents() -> None:
    assert parse_money("24.5") == Decimal("24.50")
    assert str(parse_money("24.5")) == "24.50"
    assert parse_money("0") == Decimal("0.00")


def test_blank_cells_return_none() -> None:
    assert parse_money("") is None
    assert parse_money("   ") is None
    assert parse_money(None) is None


@pytest.mark.parametrize("raw", ["12.345", "abc", "$", "USD", "1.2.3", "12 34"])
def test_invalid_money_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_money(raw)


def test_sums_are_exact() -> None:
    """Repeated addition of cents must not drift like floats do."""
    values = [Decimal("0.10")] * 10
    assert sum_money(values) == Decimal("1.00")

    assert quantize_money(Decimal("1.005")) == Decimal("1.01")


def test_float_conversion_is_boundary_only() -> None:
    value = parse_money("$129.99")
    assert value is not None
    assert money_to_float(value) == 129.99
    assert value == Decimal("129.99")


def test_dataset_totals_are_exact_decimals() -> None:
    """Every parsed CSV amount is a 2dp Decimal, never a float."""
    dataset = load_dataset(data_dir=DATA_DIR)

    for order in dataset.orders_by_id.values():
        assert isinstance(order.order_total, Decimal)
        assert isinstance(order.shipping_fee, Decimal)
        assert order.order_total == quantize_money(order.order_total)

    for lines in dataset.items_by_order.values():
        for line in lines:
            assert isinstance(line.unit_price, Decimal)
            assert line.unit_price == quantize_money(line.unit_price)

    for refunds in dataset.refunds_by_order_line.values():
        for refund in refunds:
            assert isinstance(refund.amount, Decimal)


def test_currency_prefixed_total_parses() -> None:
    """orders.csv mixes ``USD 24.00`` and ``$129.00`` in the same column."""
    dataset = load_dataset(data_dir=DATA_DIR)

    valued = [o for o in dataset.orders_by_id.values() if o.order_total > zero()]
    assert valued, "expected at least one priced order"
    assert all(o.order_total == quantize_money(o.order_total) for o in valued)
