"""Thin HTTP shell: validate input, delegate to the pipeline, persist via the ledger.

Endpoints:
``GET /health`` / ``GET /healthz``   liveness
``POST /tickets``                   one ticket object or a list (one run per batch)
``GET /runs/{run_id}``              responses and ledger for a run
``POST /approvals``                 approve/reject a pending action
``GET /ledger``                     ledger entries (optionally ``?run_id=``)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, status

from src.config import Settings, get_settings
from src.ingest import Dataset
from src.ledger import RunLedger
from src.llm import CadraClient
from src.models import Decision, LedgerEntry, Ticket, TicketResponse
from src.pipeline import dataset_for, new_run_id, process_batch

API_TITLE = "Larkfield Support Agent"
API_VERSION = "0.2.0"
APPROVABLE_OUTCOMES = frozenset({"PEND", "ESCALATE"})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Optional settings override (tests point ``runs_dir`` at a tmp dir).
    """
    config = settings or get_settings()
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    app.state.settings = config
    app.state.runs = {}
    app.state.dataset = None
    app.state.llm = (
        CadraClient(config)
        if config.cadra_token.get_secret_value().strip()
        else None
    )

    def dataset() -> Dataset:
        if app.state.dataset is None:
            app.state.dataset = dataset_for(config)
        return app.state.dataset

    def runs() -> dict[str, RunLedger]:
        return app.state.runs

    def ledger_or_404(run_id: str) -> RunLedger:
        ledger = runs().get(run_id)
        if ledger is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown run_id: {run_id}",
            )
        return ledger

    @app.get("/health", tags=["ops"])
    @app.get("/healthz", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.post(
        "/tickets",
        response_model=list[TicketResponse],
        tags=["tickets"],
    )
    def create_tickets(
        payload: Ticket | list[Ticket] = Body(...),
    ) -> list[TicketResponse]:
        """Process one ticket or a batch, in input order, under a single run."""
        tickets = [payload] if isinstance(payload, Ticket) else list(payload)
        if not tickets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="at least one ticket is required",
            )

        run_id = new_run_id()
        ledger = RunLedger(run_id, Path(config.runs_dir) / run_id)
        runs()[run_id] = ledger

        responses = process_batch(
            tickets,
            dataset=dataset(),
            ledger=ledger,
            settings=config,
            client=app.state.llm,
        )
        return [
            TicketResponse.model_validate(response.model_dump(mode="json"))
            for response in responses
        ]

    @app.get("/runs/{run_id}", tags=["runs"])
    def get_run(
        run_id: str,
        ticket_id: str | None = Query(
            default=None, description="Return just this ticket's response"
        ),
    ) -> dict[str, Any]:
        """Return a run in the contract's response shape.

        A single-ticket run returns the ticket's ``TicketResponse`` fields at the
        top level (with the full response list and ledger alongside). For a batch,
        pass ``ticket_id`` to get one response, otherwise the batch envelope.
        """
        ledger = ledger_or_404(run_id)

        if ticket_id is not None:
            response = ledger.find_response(ticket_id)
            if response is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"unknown ticket_id: {ticket_id}",
                )
            return response.model_dump(mode="json")

        payload: dict[str, Any] = {
            "run_id": ledger.run_id,
            "responses": [
                response.model_dump(mode="json") for response in ledger.responses
            ],
            "ledger": [
                entry.model_dump(mode="json") for entry in ledger.entries
            ],
        }
        if len(ledger.responses) == 1:
            payload = {**ledger.responses[0].model_dump(mode="json"), **payload}
        return payload

    @app.get("/ledger", response_model=list[LedgerEntry], tags=["ledger"])
    def get_ledger(
        run_id: str | None = Query(default=None, description="Filter to one run"),
    ) -> list[LedgerEntry]:
        """Return recorded actions, newest runs included in insertion order."""
        if run_id is not None:
            return list(ledger_or_404(run_id).entries)
        return [
            entry
            for ledger in runs().values()
            for entry in ledger.entries
        ]

    def apply_decision(
        ledger: RunLedger, decision: Decision, ticket_id: str | None
    ) -> dict[str, Any]:
        response = _select_approvable(ledger, ticket_id)
        entry = ledger.entry_for(response.ticket_id)
        if entry is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"no proposed action for ticket {response.ticket_id}",
            )

        approve = decision.decision == "approve"
        target = "APPROVED" if approve else "REJECTED"
        opposite = "REJECTED" if approve else "APPROVED"

        if entry.status == opposite:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"action already {entry.status.lower()}",
            )
        if entry.status != target:
            entry = ledger.decide(entry, approve=approve)
            ledger.save()

        return {
            "run_id": ledger.run_id,
            "ticket_id": response.ticket_id,
            "decision": decision.decision,
            "approver": decision.approver,
            "action": entry.action,
            "amount_usd": entry.amount_usd,
            "status": entry.status,
            "recorded_at": entry.recorded_at,
        }

    @app.post("/approvals", tags=["approvals"])
    def submit_approval(
        decision: Decision,
        run_id: str | None = Query(default=None),
        ticket_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Approve or reject a pending action; only PEND/ESCALATE are approvable."""
        if run_id is None:
            if not runs():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="no runs have been processed",
                )
            ledger = next(reversed(runs().values()))
        else:
            ledger = ledger_or_404(run_id)
        return apply_decision(ledger, decision, ticket_id)

    @app.post("/runs/{run_id}/decision", tags=["approvals"])
    def decide_run(
        run_id: str,
        decision: Decision,
        ticket_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Contract-compatible alias for ``POST /approvals``."""
        return apply_decision(ledger_or_404(run_id), decision, ticket_id)

    return app


def _select_approvable(ledger: RunLedger, ticket_id: str | None) -> TicketResponse:
    """Pick the requested ticket, or the newest pending one in the run."""
    if ticket_id is not None:
        response = ledger.find_response(ticket_id)
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown ticket_id: {ticket_id}",
            )
    else:
        response = next(
            (
                item
                for item in reversed(ledger.responses)
                if item.outcome in APPROVABLE_OUTCOMES
            ),
            None,
        )
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="run has no pending approvals",
            )

    if response.outcome not in APPROVABLE_OUTCOMES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"ticket {response.ticket_id} has outcome {response.outcome}; "
                "only PEND and ESCALATE can be approved"
            ),
        )
    return response


app = create_app()
