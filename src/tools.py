"""Tool layer: the only sanctioned way the decision pipeline touches data.

Every function here is deterministic and side-effect free. Names returned by
``TOOL_NAMES`` are recorded verbatim in ``trace.tools_called``.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from src.ingest import (
    CustomerRecord,
    DataRepository,
    OrderLineRecord,
    OrderRecord,
    RefundRecord,
    ShipmentRecord,
)

TOOL_NAMES: tuple[str, ...] = (
    "find_customer_by_email",
    "get_order",
    "get_order_lines",
    "get_shipment",
    "get_issued_refunds",
)


def find_customer_by_email(
    repo: DataRepository, email: str
) -> CustomerRecord | None:
    """Look up the customer that owns ``email`` (RP-3.2 4.1)."""
    raise NotImplementedError


def get_order(repo: DataRepository, order_id: str) -> OrderRecord | None:
    """Return the exact order, never a similar or nearby one (RP-3.2 4.1)."""
    raise NotImplementedError


def get_order_lines(
    repo: DataRepository, order_id: str
) -> Sequence[OrderLineRecord]:
    """Return the order lines that refunds are computed against."""
    raise NotImplementedError


def get_shipment(repo: DataRepository, order_id: str) -> ShipmentRecord | None:
    """Return the carrier record; ``None`` means status is unavailable."""
    raise NotImplementedError


def get_issued_refunds(
    repo: DataRepository, order_id: str
) -> Sequence[RefundRecord]:
    """Return prior ISSUED refunds used to cap new amounts (RP-3.2 2.5)."""
    raise NotImplementedError


def unpaid_line_value(
    line: OrderLineRecord, issued: Sequence[RefundRecord]
) -> Decimal:
    """Return the remaining refundable value of one line, never negative."""
    raise NotImplementedError
