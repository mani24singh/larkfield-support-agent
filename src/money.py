"""Money helpers: parsing and exact decimal arithmetic.

All monetary values are ``Decimal`` quantized to whole cents. Convert to
``float`` only at the JSON boundary where the supplied response schema demands
it (``LedgerEntry.amount_usd``, ``Trace.cost_usd``).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")

_MONEY_RE = re.compile(
    r"""
    ^\s*
    (?:(?P<currency>USD|\$)\s*)?
    (?P<amount>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_money(raw: str | Decimal | int | float | None) -> Decimal | None:
    """Parse a money cell into a 2dp ``Decimal``.

    Accepts plain numbers (``"210.00"``), a dollar prefix (``"$129.00"``),
    a currency code prefix (``"USD 24.00"``) and thousands separators
    (``"1,234.56"``). Blank cells return ``None``.

    Raises:
        ValueError: If the value is not a recognisable money amount.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return quantize_money(raw)
    if isinstance(raw, bool):
        raise ValueError(f"not a money value: {raw!r}")

    text = str(raw).strip()
    if not text:
        return None

    match = _MONEY_RE.match(text)
    if match is None:
        raise ValueError(f"not a money value: {raw!r}")

    try:
        amount = Decimal(match.group("amount").replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - regex already guards shape
        raise ValueError(f"not a money value: {raw!r}") from exc
    return quantize_money(amount)


def quantize_money(value: Decimal) -> Decimal:
    """Round a decimal to whole cents with half-up rounding."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_to_float(value: Decimal) -> float:
    """Convert for the JSON boundary only; never use the result in arithmetic."""
    return float(quantize_money(value))


def zero() -> Decimal:
    """Return ``0.00`` as a decimal."""
    return Decimal("0.00")


def sum_money(values: Iterable[Decimal]) -> Decimal:
    """Sum money values exactly, returning a 2dp ``Decimal``."""
    return quantize_money(sum(values, zero()))
