"""The UI talks to this boundary rather than importing agent business logic."""

from typing import Any, Protocol


class AdapterError(Exception):
    """An adapter failure with an HTTP status safe to show in the local console."""

    def __init__(self, status_code: int, detail: str):
        """Keep transport-neutral error data for the route exception handler."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Adapter(Protocol):
    """Stable local-console boundary for observable state and explicit staff actions."""

    name: str

    def metadata(self) -> dict[str, Any]:
        """Expose the mode, simulation date, supported operations and display limits."""
        ...

    def list_tickets(
        self, *, status: str | None = None, team: str | None = None,
        priority: str | None = None, channel: str | None = None,
        split: str | None = None, q: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return public inbox summaries matching the optional filters."""
        ...

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Compose one ticket detail without exposing other customers' records."""
        ...

    def is_running(self) -> bool:
        """Report whether any execution is currently in progress."""
        ...

    def start_run(self, ticket_id: str, system: str, scenario: str = "standard") -> str:
        """Schedule one background agent or baseline run and return its identifier."""
        ...

    def get_expected(self, ticket_id: str) -> dict[str, Any]:
        """Read draft expectations only for the explicitly requested grader view."""
        ...

    def list_eval_runs(self) -> list[dict[str, Any]]:
        """List benchmark snapshots with their provenance and comparison identifiers."""
        ...

    def get_eval_run(self, run_id: str) -> dict[str, Any]:
        """Read one evaluation snapshot, separately from interactive UI runs."""
        ...

    def retrieval_metrics(self) -> dict[str, Any]:
        """Read retrieval metrics with explicit measured/mock provenance."""
        ...

    def start_resume(self, ticket_id: str) -> str:
        """Continue a saved, resumable ticket using its remaining lifetime budget."""
        ...

    def get_trace(self, ticket_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Return events after the event-count cursor without changing execution."""
        ...

    def list_approvals(self) -> dict[str, Any]:
        """Return waiting tickets and explicit staged staff approvals."""
        ...

    def approve(self, approval_id: str) -> dict[str, Any]:
        """Activate one known staged approval idempotently as a staff operation."""
        ...

    def list_policies(self) -> list[dict[str, Any]]:
        """List policy identities and versions for the policy browser."""
        ...

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        """Return the policy metadata and its four markdown sections."""
        ...

    def reset(self) -> dict[str, int]:
        """Restore this adapter's working state and report the record counts."""
        ...
