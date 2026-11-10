"""Thin HTTP shell implementing the CONTRACT §2 endpoints.

Handlers validate input with ``src.models``, delegate to the pipeline or ledger,
and never contain decision logic of their own.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, status

from src.config import Settings, get_settings
from src.models import Decision, LedgerEntry, Ticket, TicketResponse

API_TITLE = "Larkfield Support Agent"
API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Optional settings override (tests pass a fixed instance).
    """
    config = settings or get_settings()
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    app.state.settings = config

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe required by the contract."""
        return {"status": "ok"}

    @app.post(
        "/tickets",
        response_model=TicketResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["tickets"],
    )
    def create_ticket(ticket: Ticket) -> TicketResponse:
        """Process one incoming ticket and return the structured decision."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="ticket processing is not implemented yet",
        )

    @app.get("/runs/{run_id}", response_model=TicketResponse, tags=["runs"])
    def get_run(run_id: str) -> TicketResponse:
        """Return the current run, including any pending human approval."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="run retrieval is not implemented yet",
        )

    @app.post("/runs/{run_id}/decision", tags=["runs"])
    def decide(run_id: str, decision: Decision) -> dict[str, Any]:
        """Approve or reject a proposed action, idempotently."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="approval decisions are not implemented yet",
        )

    @app.get("/ledger", response_model=list[LedgerEntry], tags=["ledger"])
    def get_ledger() -> list[LedgerEntry]:
        """Return every recorded action."""
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="ledger read model is not implemented yet",
        )

    return app
