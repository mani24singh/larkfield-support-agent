"""Append-only run and action ledger with atomic persistence.

Each batch of tickets is one run. Responses are written to
``runs/<run_id>/responses.json`` and actions append to
``runs/<run_id>/ledger.jsonl``; both are replaced atomically via a temporary
file in the same directory.

Repeated approval decisions never create a second financial action: entries are
keyed by ticket and action, and refund idempotency keys are tracked per run.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, Sequence

from src.models import LedgerEntry, TicketResponse

RefundKey = tuple[str, str, int]
"""Idempotency key: (normalised email, order id, affected line number)."""

LedgerAction = Literal["REFUND", "REPLY", "ESCALATION"]

APPROVABLE_ACTIONS = ("REFUND", "ESCALATION")


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Immutable snapshot of a run."""

    run_id: str
    responses: tuple[TicketResponse, ...]
    entries: tuple[LedgerEntry, ...]


def idempotency_key(email: str, order_id: str, line_no: int) -> RefundKey:
    """Return the stable key that stops a duplicate refund for the same line."""
    return (email.strip().lower(), order_id.strip().upper(), int(line_no))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=".tmp-", suffix=path.suffix
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass
class RunLedger:
    """In-memory run state mirrored to an append-only directory."""

    run_id: str
    directory: Path
    _responses: list[TicketResponse] = field(default_factory=list)
    _entries: list[LedgerEntry] = field(default_factory=list)
    _refund_keys: dict[RefundKey, str] = field(default_factory=dict)

    # -- responses ---------------------------------------------------------
    @property
    def responses(self) -> Sequence[TicketResponse]:
        return tuple(self._responses)

    def find_response(self, ticket_id: str) -> TicketResponse | None:
        return next(
            (item for item in self._responses if item.ticket_id == ticket_id), None
        )

    def add_response(self, response: TicketResponse) -> None:
        if response.run_id != self.run_id:
            raise ValueError(
                f"response run_id {response.run_id} does not match {self.run_id}"
            )
        self._responses.append(response)

    # -- idempotency -------------------------------------------------------
    def ticket_for_refund(self, key: RefundKey) -> str | None:
        """Return the ticket that already owns this refund key in this run.

        A rejected refund frees the key, so only PROPOSED/APPROVED actions block
        a repeat request.
        """
        ticket_id = self._refund_keys.get(key)
        if ticket_id is None:
            return None
        entry = self.entry_for(ticket_id)
        if entry is not None and entry.status in ("PROPOSED", "APPROVED"):
            return ticket_id
        return None

    def register_refund(self, key: RefundKey, ticket_id: str) -> None:
        self._refund_keys.setdefault(key, ticket_id)

    # -- entries -----------------------------------------------------------
    @property
    def entries(self) -> Sequence[LedgerEntry]:
        return tuple(self._entries)

    def entry_for(self, ticket_id: str) -> LedgerEntry | None:
        """Return the ticket's financial action, if it has one."""
        return next(
            (
                entry
                for entry in self._entries
                if entry.ticket_id == ticket_id
                and entry.action in APPROVABLE_ACTIONS
            ),
            None,
        )

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        """Append an entry once; repeating the same action is a no-op."""
        if entry.run_id != self.run_id:
            raise ValueError(f"entry run_id {entry.run_id} does not match {self.run_id}")
        for existing in self._entries:
            if (
                existing.ticket_id == entry.ticket_id
                and existing.action == entry.action
            ):
                return existing
        self._entries.append(entry)
        return entry

    def decide(self, entry: LedgerEntry, *, approve: bool) -> LedgerEntry:
        """Set APPROVED/REJECTED and persist; replaying a decision is a no-op."""
        target = "APPROVED" if approve else "REJECTED"
        if entry.status == target:
            return entry
        updated = entry.model_copy(update={"status": target})
        self._entries = [
            updated if item is entry else item for item in self._entries
        ]
        self.save()
        return updated

    # -- persistence -------------------------------------------------------
    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        responses = json.dumps(
            [item.model_dump(mode="json") for item in self._responses],
            indent=2,
            ensure_ascii=False,
        )
        _atomic_write(self.directory / "responses.json", responses + "\n")

        ledger_text = "".join(
            entry.model_dump_json() + "\n" for entry in self._entries
        )
        _atomic_write(self.directory / "ledger.jsonl", ledger_text)

    def snapshot(self) -> RunRecord:
        return RunRecord(
            run_id=self.run_id,
            responses=tuple(self._responses),
            entries=tuple(self._entries),
        )


def build_entry(
    *,
    run_id: str,
    ticket_id: str,
    action: LedgerAction,
    amount_usd: Decimal | None,
) -> LedgerEntry:
    """Create a PROPOSED ledger entry; amounts only exist for REFUND rows."""
    return LedgerEntry(
        run_id=run_id,
        ticket_id=ticket_id,
        action=action,
        amount_usd=float(amount_usd) if amount_usd is not None else None,
        status="PROPOSED",
        recorded_at=_now(),
    )
