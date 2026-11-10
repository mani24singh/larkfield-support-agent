"""Read-only access to the supplied CSV data pack.

The data is a raw export (see ``DATA_DICTIONARY.md``) and must not be modified
in place: loaders normalize in memory only. Money is parsed as ``Decimal`` and
timestamps as timezone-aware UTC datetimes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True, slots=True)
class CustomerRecord:
    """One row of ``data/customers.csv``."""

    customer_id: str
    name: str
    email: str
    country: str
    created_at: datetime
    loyalty_tier: str


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """One row of ``data/orders.csv`` (note the ``Order Total`` header)."""

    order_id: str
    customer_id: str
    order_date: datetime
    status: str
    order_total: Decimal
    shipping_fee: Decimal
    currency: str
    payment_method: str


@dataclass(frozen=True, slots=True)
class OrderLineRecord:
    """One row of ``data/order_items.csv``."""

    order_id: str
    line_no: int
    sku: str
    product_name: str
    qty: int
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class ShipmentRecord:
    """One row of ``data/shipments.csv``; timestamps may be ``None``."""

    shipment_id: str
    order_id: str
    carrier: str
    tracking_no: str
    status: str
    shipped_at: datetime | None
    delivered_at: datetime | None
    last_event_at: datetime | None


@dataclass(frozen=True, slots=True)
class RefundRecord:
    """One row of ``data/refunds.csv`` (refunds already issued)."""

    refund_id: str
    order_id: str
    line_no: int
    amount: Decimal
    status: str
    requested_at: datetime
    approved_by: str


class DataRepository(Protocol):
    """Query facade over the loaded data pack."""

    def customer_by_email(self, email: str) -> CustomerRecord | None:
        """Return the customer whose email on file matches, else ``None``."""

    def order(self, order_id: str) -> OrderRecord | None:
        """Return the order with this exact id, else ``None``."""

    def order_lines(self, order_id: str) -> Sequence[OrderLineRecord]:
        """Return every line of the order, ordered by ``line_no``."""

    def shipment_for_order(self, order_id: str) -> ShipmentRecord | None:
        """Return the shipment for the order, else ``None``."""

    def issued_refunds(self, order_id: str) -> Sequence[RefundRecord]:
        """Return refunds already issued for the order."""


def load_repository(settings_paths: object) -> DataRepository:
    """Load the CSV pack into an indexed, in-memory repository.

    Args:
        settings_paths: Configured file locations for each dataset.

    Returns:
        A read-only ``DataRepository``.
    """
    raise NotImplementedError("data loading is implemented in the next milestone")


def load_tickets(path: Path) -> Sequence[dict[str, object]]:
    """Load a JSON ticket file (inbox export or public inbox) as raw payloads."""
    raise NotImplementedError("ticket loading is implemented in the next milestone")


def load_labelled_seed(path: Path) -> Sequence[dict[str, object]]:
    """Load the labelled evaluation seed used to measure decision quality."""
    raise NotImplementedError("seed loading is implemented in the next milestone")
