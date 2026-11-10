"""Run and action ledger with idempotent refund semantics.

A run is created per processed ticket. Proposed actions are recorded as
``PROPOSED`` and only move to ``APPROVED``/``REJECTED`` via the decision
endpoint. Replaying the same decision must never create a second financial
action (CONTRACT §6, RP-3.2 3.3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from src.models import LedgerEntry, TicketResponse


@dataclass(frozen=True, slots=True)
class RunRecord:
    """A processed ticket plus its pending approval state."""

    run_id: str
    response: TicketResponse
    proposed_amount_usd: float | None
    decided: bool = False


class LedgerStore(Protocol):
    """Persistent-enough state for runs and ledger entries."""

    def save_run(self, run: RunRecord) -> None:
        """Store a run so ``GET /runs/{run_id}`` can return it."""

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return the run, or ``None`` when unknown."""

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        """Record an action idempotently and return the stored entry."""

    def entries(self) -> Sequence[LedgerEntry]:
        """Return every recorded action in insertion order."""


class InMemoryLedger:
    """Default store used for local runs and tests."""

    def save_run(self, run: RunRecord) -> None:
        raise NotImplementedError("run storage is implemented in the next milestone")

    def get_run(self, run_id: str) -> RunRecord | None:
        raise NotImplementedError

    def record(self, entry: LedgerEntry) -> LedgerEntry:
        raise NotImplementedError

    def entries(self) -> Sequence[LedgerEntry]:
        raise NotImplementedError


def idempotency_key(run_id: str, ticket_id: str, action: str) -> str:
    """Return the stable key that prevents duplicate financial actions."""
    raise NotImplementedError
