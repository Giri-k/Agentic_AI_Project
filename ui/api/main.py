"""Thin local HTTP routes; adapters own fixture access and future agent integration."""

import os
from importlib import import_module
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .adapter import Adapter, AdapterError
from .mock_adapter import MockAdapter

Status = Literal["new", "resolved", "closed", "awaiting_customer", "awaiting_approval", "escalated", "failed_safe"]
Team = Literal["Delivery", "Returns", "Billing", "Human Review"]
Priority = Literal["low", "medium", "high", "critical"]
Channel = Literal["email", "chat", "phone", "social_media"]
Split = Literal["dev", "test", "demo"]


class RunRequest(BaseModel):
    """Only the comparison system is a browser input; execution details stay server-owned."""

    model_config = ConfigDict(extra="forbid")
    system: Literal["agent", "baseline"] = "agent"
    scenario: Literal["standard", "timeout_retry", "repeated_call", "refund_gate"] = "standard"


def _configured_adapter() -> Adapter:
    """Choose the requested mode explicitly; a missing real adapter never falls back to mock."""
    mode = os.environ.get("OPUS_ADAPTER", "mock").strip().lower()
    if mode == "mock":
        return MockAdapter()
    if mode == "real":
        try:
            module = import_module("ui.api.real_adapter")
            adapter_class = module.RealAdapter
        except (ImportError, AttributeError) as error:
            raise AdapterError(503, "Real adapter is not available yet. Set OPUS_ADAPTER=mock to use the local fixture preview.") from error
        return adapter_class()
    raise AdapterError(503, "OPUS_ADAPTER must be 'mock' or 'real'.")


def _adapter(request: Request) -> Adapter:
    """Resolve the app-owned adapter or report an unavailable configured mode."""
    if request.app.state.adapter_error is not None:
        raise request.app.state.adapter_error
    return request.app.state.adapter


def create_app(adapter: Adapter | None = None) -> FastAPI:
    """Construct an app with an injectable adapter for isolated local tests."""
    app = FastAPI(title="OP-US Support Console", version="0.1.0")
    app.state.adapter_error = None
    try:
        app.state.adapter = adapter if adapter is not None else _configured_adapter()
    except AdapterError as error:
        app.state.adapter = None
        app.state.adapter_error = error
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AdapterError)
    async def adapter_error_handler(request: Request, error: AdapterError) -> JSONResponse:
        """Keep adapter failures consistent with the documented detail-message contract."""
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/api/health")
    def health(source: Annotated[Adapter, Depends(_adapter)]):
        """Expose mode and capabilities so the UI enables only supported operations."""
        return source.metadata()

    @app.get("/api/tickets")
    def tickets(
        source: Annotated[Adapter, Depends(_adapter)],
        status: Status | None = None, team: Team | None = None,
        priority: Priority | None = None, channel: Channel | None = None,
        split: Split | None = None, q: str | None = None,
    ):
        """Pass validated inbox filters through the adapter boundary."""
        return source.list_tickets(status=status, team=team, priority=priority, channel=channel, split=split, q=q)

    @app.get("/api/tickets/{ticket_id}")
    def ticket_detail(ticket_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Return one public ticket detail or the adapter's descriptive 404."""
        return source.get_ticket(ticket_id)

    @app.post("/api/tickets/{ticket_id}/run", status_code=202)
    def run_ticket(ticket_id: str, body: RunRequest, source: Annotated[Adapter, Depends(_adapter)]):
        """Start work in the adapter's background worker instead of blocking an HTTP request."""
        return {"run_id": source.start_run(ticket_id, body.system, body.scenario)}

    @app.post("/api/tickets/{ticket_id}/resume", status_code=202)
    def resume_ticket(ticket_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Resume only when the adapter confirms its saved requirement is satisfied."""
        return {"run_id": source.start_resume(ticket_id)}

    @app.get("/api/tickets/{ticket_id}/trace")
    def trace(ticket_id: str, source: Annotated[Adapter, Depends(_adapter)], since: int = Query(default=0, ge=0)):
        """Read append-only trace events using a nonnegative event-count cursor."""
        return source.get_trace(ticket_id, since)

    @app.get("/api/tickets/{ticket_id}/expected")
    def expected(ticket_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Expose draft labels only through the explicitly opted-in grader endpoint."""
        return source.get_expected(ticket_id)

    @app.get("/api/approvals")
    def approvals(source: Annotated[Adapter, Depends(_adapter)]):
        """List waiting work and staged approvals without implicitly activating one."""
        return source.list_approvals()

    @app.post("/api/approvals/{approval_id}/approve")
    def approve(approval_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Make the explicit staff choice active; repeated requests are harmless."""
        return source.approve(approval_id)

    @app.get("/api/policies")
    def policies(source: Annotated[Adapter, Depends(_adapter)]):
        """List the indexed policies available to the local viewer."""
        return source.list_policies()

    @app.get("/api/policies/{policy_id}")
    def policy_detail(policy_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Return one policy's metadata and markdown sections."""
        return source.get_policy(policy_id)

    @app.get("/api/eval/runs")
    def eval_runs(source: Annotated[Adapter, Depends(_adapter)]):
        """List evaluation snapshots without starting or changing a run."""
        return source.list_eval_runs()

    @app.get("/api/eval/runs/{run_id}")
    def eval_run(run_id: str, source: Annotated[Adapter, Depends(_adapter)]):
        """Return a snapshot whose provenance distinguishes fixtures from real measurements."""
        return source.get_eval_run(run_id)

    @app.get("/api/eval/retrieval")
    def retrieval(source: Annotated[Adapter, Depends(_adapter)]):
        """Return retrieval measurements or clearly labeled illustrative mock rankings."""
        return source.retrieval_metrics()

    @app.post("/api/admin/reset")
    def reset(source: Annotated[Adapter, Depends(_adapter)]):
        """Reset only adapter-owned state, refusing to interrupt a running execution."""
        if source.is_running():
            raise HTTPException(409, "A run is in progress; wait for it to finish before resetting.")
        return source.reset()

    return app


app = create_app()
