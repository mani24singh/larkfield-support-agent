"""Read-only access to the supplied CSV data pack.

The files in ``data/`` are a raw export (see ``DATA_DICTIONARY.md``) and must
never be modified in place: everything is normalised in memory. Money becomes
``Decimal`` cents, dates become timezone-aware UTC datetimes, emails are
case/space insensitive and order references are canonicalised to ``LF-######``.

Known export quirks handled here:

* ``orders.csv`` repeats two order rows verbatim; identical duplicates are
  collapsed and logged, conflicting duplicates raise.
* ``order_date`` mixes ISO (``2026-09-12``) and slash dates (``26/08/2026``,
  ``08/10/2026``). Day-first is assumed, and genuinely ambiguous slash dates
  are surfaced in ``Dataset.ambiguous_dates``.
* ``orders.csv`` names one column ``Order Total`` (with a space).
* ``shipments.csv`` has blank ``delivered_at`` / ``last_event_at`` cells and
  ``UNAVAILABLE`` statuses; both are represented honestly.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeVar

from src.config import Settings, get_settings
from src.money import parse_money

logger = logging.getLogger(__name__)

DATE_FORMATS_DAY_FIRST = ("%d/%m/%Y", "%d-%m-%Y")
DATE_FORMATS_MONTH_FIRST = ("%m/%d/%Y", "%m-%d-%Y")

_ORDER_PREFIX_RE = re.compile(r"\blf\s*[-–—]?\s*(\d{6})\b", re.IGNORECASE)
_ORDER_BARE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

K = TypeVar("K")
V = TypeVar("V")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class AmbiguousDate:
    """A slash date that parses both day-first and month-first, differently."""

    source: str
    record_id: str
    raw: str
    resolved: date
    alternative: date


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------


def normalise_email(raw: str | None) -> str:
    """Return a comparison-safe email: stripped and lowercased."""
    if raw is None:
        return ""
    return str(raw).strip().lower()


def normalise_order_id(raw: str | None) -> str | None:
    """Canonicalise a customer-written order reference to ``LF-######``.

    Accepts ``LF-100200``, ``LF100200``, ``#100200``, bare ``100200`` and prose
    such as ``"order order 100200"``. Returns ``None`` when no unambiguous
    six-digit reference is present — a near-miss must never resolve to a
    neighbouring order (RP-3.2 4.1).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    match = _ORDER_PREFIX_RE.search(text)
    if match is not None:
        return f"LF-{match.group(1)}"

    match = _ORDER_BARE_RE.search(text)
    if match is not None:
        return f"LF-{match.group(1)}"
    return None


def _try_date_formats(text: str, formats: Sequence[str]) -> date | None:
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_date_detail(raw: str | None) -> tuple[date | None, date | None]:
    """Parse a date cell and return ``(value, alternative_reading)``.

    ``alternative_reading`` is only set when a slash date parses validly both
    day-first and month-first to two different dates.
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date(), None
    except ValueError:
        pass

    day_first = _try_date_formats(text, DATE_FORMATS_DAY_FIRST)
    month_first = _try_date_formats(text, DATE_FORMATS_MONTH_FIRST)

    if day_first is not None and month_first is not None and day_first != month_first:
        return day_first, month_first
    if day_first is not None:
        return day_first, None
    if month_first is not None:
        return month_first, None
    raise ValueError(f"unrecognised date: {raw!r}")


def parse_date(raw: str | None) -> tuple[date | None, bool]:
    """Parse ``YYYY-MM-DD``, then ``%d/%m/%Y``, then ``%m/%d/%Y``.

    Returns:
        ``(date, is_ambiguous)``. ``is_ambiguous`` is true only when both
        slash interpretations are valid and disagree; the day-first reading
        is returned.
    """
    parsed, alternative = parse_date_detail(raw)
    return parsed, alternative is not None


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse a timestamp cell into a timezone-aware UTC datetime.

    Naive ISO timestamps are assumed UTC. Blank cells return ``None``.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed, _ = parse_date_detail(text)
        if parsed is None:  # pragma: no cover - parse_date_detail raises first
            return None
        value = datetime.combine(parsed, time.min)

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _date_at_utc_midnight(raw: str | None) -> datetime:
    parsed, _ = parse_date_detail(raw)
    if parsed is None:
        raise ValueError(f"missing date where one is required: {raw!r}")
    return datetime.combine(parsed, time.min, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dataset:
    """Indexed, read-only view of the supplied data pack."""

    customers_by_email: Mapping[str, CustomerRecord]
    customers_by_id: Mapping[str, CustomerRecord]
    orders_by_id: Mapping[str, OrderRecord]
    items_by_order: Mapping[str, tuple[OrderLineRecord, ...]]
    shipments_by_order: Mapping[str, ShipmentRecord]
    refunds_by_order_line: Mapping[tuple[str, int], tuple[RefundRecord, ...]]
    duplicate_order_ids: tuple[str, ...] = ()
    ambiguous_dates: tuple[AmbiguousDate, ...] = ()

    # -- DataRepository protocol -------------------------------------------
    def customer_by_email(self, email: str) -> CustomerRecord | None:
        """Return the customer whose email on file matches, else ``None``."""
        return self.customers_by_email.get(normalise_email(email))

    def customer(self, customer_id: str) -> CustomerRecord | None:
        """Return the customer by CRM key, else ``None``."""
        return self.customers_by_id.get(str(customer_id).strip())

    def order(self, order_id: str) -> OrderRecord | None:
        """Return the exact order for a canonical or customer-written id."""
        canonical = normalise_order_id(order_id)
        if canonical is None:
            return None
        return self.orders_by_id.get(canonical)

    def order_lines(self, order_id: str) -> tuple[OrderLineRecord, ...]:
        """Return every line of the order, ordered by ``line_no``."""
        canonical = normalise_order_id(order_id)
        if canonical is None:
            return ()
        return self.items_by_order.get(canonical, ())

    def shipment_for_order(self, order_id: str) -> ShipmentRecord | None:
        """Return the shipment for the order, else ``None``."""
        canonical = normalise_order_id(order_id)
        if canonical is None:
            return None
        return self.shipments_by_order.get(canonical)

    def issued_refunds(self, order_id: str) -> tuple[RefundRecord, ...]:
        """Return refunds already issued for the order, by line then id."""
        canonical = normalise_order_id(order_id)
        if canonical is None:
            return ()
        rows: list[RefundRecord] = []
        for (line_order_id, _line_no), refunds in self.refunds_by_order_line.items():
            if line_order_id == canonical:
                rows.extend(refunds)
        return tuple(sorted(rows, key=lambda r: (r.line_no, r.refund_id)))


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


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_customers(path: Path) -> tuple[dict[str, CustomerRecord], dict[str, CustomerRecord], list[AmbiguousDate]]:
    by_email: dict[str, CustomerRecord] = {}
    by_id: dict[str, CustomerRecord] = {}
    ambiguous: list[AmbiguousDate] = []

    for row in _read_rows(path):
        created, alternative = parse_date_detail(row.get("created_at"))
        if created is None:
            raise ValueError(f"customers.csv: missing created_at for {row.get('customer_id')}")
        if alternative is not None:
            ambiguous.append(
                AmbiguousDate(
                    source="customers.csv:created_at",
                    record_id=row["customer_id"],
                    raw=str(row["created_at"]),
                    resolved=created,
                    alternative=alternative,
                )
            )

        record = CustomerRecord(
            customer_id=row["customer_id"].strip(),
            name=row["name"].strip(),
            email=normalise_email(row["email"]),
            country=row["country"].strip().upper(),
            created_at=datetime.combine(created, time.min, tzinfo=timezone.utc),
            loyalty_tier=row["loyalty_tier"].strip().lower() or "none",
        )

        if record.customer_id in by_id:
            raise ValueError(f"customers.csv: duplicate customer_id {record.customer_id}")
        by_id[record.customer_id] = record

        existing = by_email.get(record.email)
        if existing is not None and existing != record:
            logger.warning(
                "customers.csv: email %s is shared by %s and %s; keeping the first",
                record.email,
                existing.customer_id,
                record.customer_id,
            )
        else:
            by_email.setdefault(record.email, record)

    return by_email, by_id, ambiguous


def _load_orders(
    path: Path,
) -> tuple[dict[str, OrderRecord], list[str], list[AmbiguousDate]]:
    orders: dict[str, OrderRecord] = {}
    duplicates: list[str] = []
    ambiguous: list[AmbiguousDate] = []

    for row in _read_rows(path):
        order_date, alternative = parse_date_detail(row.get("order_date"))
        if order_date is None:
            raise ValueError(f"orders.csv: missing order_date for {row.get('order_id')}")
        if alternative is not None:
            ambiguous.append(
                AmbiguousDate(
                    source="orders.csv:order_date",
                    record_id=row["order_id"].strip(),
                    raw=str(row["order_date"]),
                    resolved=order_date,
                    alternative=alternative,
                )
            )

        order_total = parse_money(row.get("Order Total"))
        shipping_fee = parse_money(row.get("shipping_fee"))
        record = OrderRecord(
            order_id=row["order_id"].strip(),
            customer_id=row["customer_id"].strip(),
            order_date=datetime.combine(order_date, time.min, tzinfo=timezone.utc),
            status=row["status"].strip().upper(),
            order_total=order_total if order_total is not None else Decimal("0.00"),
            shipping_fee=shipping_fee if shipping_fee is not None else Decimal("0.00"),
            currency=(row.get("currency") or "USD").strip().upper(),
            payment_method=(row.get("payment_method") or "").strip().lower(),
        )

        existing = orders.get(record.order_id)
        if existing is None:
            orders[record.order_id] = record
            continue

        if existing != record:
            raise ValueError(
                "orders.csv: conflicting duplicate rows for "
                f"{record.order_id}: {existing!r} != {record!r}"
            )
        duplicates.append(record.order_id)
        logger.warning(
            "orders.csv: identical duplicate row for %s collapsed", record.order_id
        )

    return orders, duplicates, ambiguous


def _load_items(path: Path) -> dict[str, tuple[OrderLineRecord, ...]]:
    grouped: dict[str, list[OrderLineRecord]] = {}
    seen: set[tuple[str, int]] = set()

    for row in _read_rows(path):
        record = OrderLineRecord(
            order_id=row["order_id"].strip(),
            line_no=int(row["line_no"]),
            sku=row["sku"].strip(),
            product_name=row["product_name"].strip(),
            qty=int(row["qty"]),
            unit_price=parse_money(row["unit_price"]) or Decimal("0.00"),
        )
        key = (record.order_id, record.line_no)
        if key in seen:
            raise ValueError(f"order_items.csv: duplicate line {record.order_id}#{record.line_no}")
        seen.add(key)
        grouped.setdefault(record.order_id, []).append(record)

    return {
        order_id: tuple(sorted(lines, key=lambda line: line.line_no))
        for order_id, lines in grouped.items()
    }


def _load_shipments(path: Path) -> dict[str, ShipmentRecord]:
    by_order: dict[str, ShipmentRecord] = {}

    for row in _read_rows(path):
        record = ShipmentRecord(
            shipment_id=row["shipment_id"].strip(),
            order_id=row["order_id"].strip(),
            carrier=row["carrier"].strip(),
            tracking_no=row["tracking_no"].strip(),
            status=row["status"].strip().upper(),
            shipped_at=parse_timestamp(row.get("shipped_at")),
            delivered_at=parse_timestamp(row.get("delivered_at")),
            last_event_at=parse_timestamp(row.get("last_event_at")),
        )
        existing = by_order.get(record.order_id)
        if existing is not None and existing != record:
            logger.warning(
                "shipments.csv: multiple shipments for %s; keeping %s",
                record.order_id,
                existing.shipment_id,
            )
            continue
        by_order.setdefault(record.order_id, record)

    return by_order


def _load_refunds(path: Path) -> dict[tuple[str, int], tuple[RefundRecord, ...]]:
    grouped: dict[tuple[str, int], list[RefundRecord]] = {}

    for row in _read_rows(path):
        requested_at = parse_timestamp(row.get("requested_at"))
        if requested_at is None:
            raise ValueError(f"refunds.csv: missing requested_at for {row.get('refund_id')}")
        record = RefundRecord(
            refund_id=row["refund_id"].strip(),
            order_id=row["order_id"].strip(),
            line_no=int(row["line_no"]),
            amount=parse_money(row["amount"]) or Decimal("0.00"),
            status=row["status"].strip().upper(),
            requested_at=requested_at,
            approved_by=row["approved_by"].strip(),
        )
        grouped.setdefault((record.order_id, record.line_no), []).append(record)

    return {
        key: tuple(sorted(rows, key=lambda refund: refund.refund_id))
        for key, rows in grouped.items()
    }


def _freeze(mapping: dict[K, V]) -> Mapping[K, V]:
    return MappingProxyType(mapping)


def load_dataset(
    settings: Settings | None = None, *, data_dir: Path | None = None
) -> Dataset:
    """Load every CSV in the data pack into an indexed ``Dataset``.

    Args:
        settings: Runtime settings; defaults to ``get_settings()``.
        data_dir: Optional directory override for all five files.
    """
    config = settings or get_settings()

    if data_dir is not None:
        base = Path(data_dir)
        paths = {
            "customers": base / "customers.csv",
            "orders": base / "orders.csv",
            "items": base / "order_items.csv",
            "shipments": base / "shipments.csv",
            "refunds": base / "refunds.csv",
        }
    else:
        base = Path(config.data_dir)
        paths = {
            "customers": Path(config.customers_csv) if config.customers_csv else base / "customers.csv",
            "orders": Path(config.orders_csv) if config.orders_csv else base / "orders.csv",
            "items": Path(config.order_items_csv) if config.order_items_csv else base / "order_items.csv",
            "shipments": Path(config.shipments_csv) if config.shipments_csv else base / "shipments.csv",
            "refunds": Path(config.refunds_csv) if config.refunds_csv else base / "refunds.csv",
        }

    customers_by_email, customers_by_id, customer_dates = _load_customers(paths["customers"])
    orders_by_id, duplicate_order_ids, order_dates = _load_orders(paths["orders"])

    for order in orders_by_id.values():
        if order.customer_id not in customers_by_id:
            logger.warning(
                "orders.csv: %s references unknown customer %s",
                order.order_id,
                order.customer_id,
            )

    return Dataset(
        customers_by_email=_freeze(customers_by_email),
        customers_by_id=_freeze(customers_by_id),
        orders_by_id=_freeze(orders_by_id),
        items_by_order=_freeze(_load_items(paths["items"])),
        shipments_by_order=_freeze(_load_shipments(paths["shipments"])),
        refunds_by_order_line=_freeze(_load_refunds(paths["refunds"])),
        duplicate_order_ids=tuple(duplicate_order_ids),
        ambiguous_dates=tuple(customer_dates + order_dates),
    )


def load_repository(settings: Settings | None = None) -> DataRepository:
    """Return the data pack behind the ``DataRepository`` interface."""
    return load_dataset(settings)


# ---------------------------------------------------------------------------
# Ticket files
# ---------------------------------------------------------------------------


def load_tickets(path: Path) -> Sequence[dict[str, object]]:
    """Load a JSON ticket file (inbox export or public inbox) as raw payloads."""
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a JSON list of tickets")
    return payload


def load_labelled_seed(path: Path) -> Sequence[dict[str, object]]:
    """Load the labelled evaluation seed used to measure decision quality."""
    return load_tickets(path)
