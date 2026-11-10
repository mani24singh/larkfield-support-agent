"""Orchestration: ticket payload in, schema-valid ``TicketResponse`` out.

The pipeline ties together classification, data lookup, policy evaluation,
drafting and tracing. It is the only place that assembles a response, and it
validates against ``src.models.TicketResponse`` before returning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from src.config import Settings
from src.models import Ticket, TicketResponse


class Clock(Protocol):
    """Injectable clock so window tests are deterministic."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""


class ModelClient(Protocol):
    """Minimal LLM surface used for classification and drafting only."""

    def classify(self, ticket: Ticket) -> str:
        """Return one of the documented ticket types."""

    def draft_reply(self, context: str) -> str:
        """Return a customer-facing draft; never sends anything."""


def process_ticket(
    ticket: Ticket,
    *,
    settings: Settings,
    clock: Clock | None = None,
    model: ModelClient | None = None,
) -> TicketResponse:
    """Process one ticket and return a validated decision plus trace.

    Args:
        ticket: Validated inbound ticket.
        settings: Runtime settings (data paths, thresholds, provider config).
        clock: Optional clock override for deterministic tests.
        model: Optional LLM client; the pipeline must work without one.

    Returns:
        A ``TicketResponse`` that has passed pydantic validation.

    Raises:
        NotImplementedError: Until the decision pipeline is implemented.
    """
    raise NotImplementedError("ticket processing is implemented in the next milestone")


def new_run_id() -> str:
    """Return a unique run identifier (never derived from ticket content)."""
    raise NotImplementedError
