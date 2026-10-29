"""The only path from a model tool call to a tool.

For each call: stop a 3rd identical call, refuse unknown tools and bad arguments (free),
enforce the one-retry rule, check the tool budget, reuse saved receipts for repeated
actions, simulate the ticket's injected failure, run the tool, save, and trace.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from pydantic import ValidationError

from .config import IDENTICAL_CALL_STOP, MAX_RETRIES, MAX_TOOL_CALLS
from .schemas import Category, Status, StopRun, TicketResult, ToolError
from .tools import TOOLS, Context, TicketUpdateArgs, openai_tools, ticket_update


@dataclass
class Budget:
    """Per-ticket counters. Saved in the checkpoint, so limits hold across pause and resume."""
    model_calls: int = 0
    tool_calls: int = 0
    schema_repairs: int = 0
    revisions: int = 0
    retries: int = 0
    seen: dict = field(default_factory=dict)          # call signature -> times the model asked for it
    awaiting_retry: list = field(default_factory=list)  # signatures whose last result was a retryable error
    fault: dict | None = None                          # injected failure still to fire, if any

    @classmethod
    def for_ticket(cls, store, ticket_id):
        """Fresh counters, plus this ticket's injected tool failure (model/embedding faults are handled elsewhere)."""
        injected = store.failure_injection(ticket_id) or {}
        fault = None
        if "tool" in injected:
            times = injected["fail_times"]
            fault = {"tool": injected["tool"], "error": injected["error"], "retryable": injected["retryable"],
                     "remaining": -1 if times == "all" else times}
        return cls(fault=fault)

    def to_dict(self):
        """Plain dict for the checkpoint file."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Rebuild counters from a checkpoint on resume."""
        return cls(**data)


def canonical(raw_args):
    """Same arguments in any key order or spacing give the same text; unparseable input is kept as-is."""
    try:
        return json.dumps(json.loads(raw_args), sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return str(raw_args)


def action_key(ticket_id, tool, args):
    """Idempotency key built by Python: ticket + tool + exact arguments."""
    digest = hashlib.sha256(json.dumps(args.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:16]
    return f"{ticket_id}:{tool}:{digest}"


class Dispatcher:
    """Runs the model's tool calls for one ticket under the budget, permission and idempotency rules."""

    def __init__(self, store, ticket_id, budget=None, trace=None, retriever=None):
        if ticket_id not in store.tickets:
            raise StopRun("unknown_ticket")
        self.store, self.ticket_id = store, ticket_id
        self.customer_id = store.tickets[ticket_id]["customer_id"]
        self.budget = budget or Budget.for_ticket(store, ticket_id)
        self.trace, self.retriever = trace, retriever
        self._n = 0

    def tools_for_model(self):
        """The tool list sent to the model (native function calling)."""
        return openai_tools()

    def call(self, name, raw_args):
        """Handle one tool call from the model. Returns {"ok": True, "result": ...} or {"ok": False, "error": ...};
        raises StopRun when the run must end."""
        self._n += 1
        call_id = f"{self.trace.run_id if self.trace else 'run'}-t{self._n}"
        sig = f"{name}:{canonical(raw_args)}"
        self._log("tool_request", call_id=call_id, tool=name, args=canonical(raw_args))

        self.budget.seen[sig] = self.budget.seen.get(sig, 0) + 1
        if self.budget.seen[sig] >= IDENTICAL_CALL_STOP:
            self._log("stop", call_id=call_id, reason="repeated_call", tool=name)
            raise StopRun("repeated_call")

        spec = TOOLS.get(name)
        if spec is None:
            return self._refuse(call_id, name, ToolError("UNKNOWN_TOOL", f"no tool named {name!r}; use one of {sorted(TOOLS)}"))
        try:
            args = spec.args.model_validate_json(raw_args)
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:3])
            return self._refuse(call_id, name, ToolError("INVALID_ARGS", problems))

        is_retry = sig in self.budget.awaiting_retry
        if is_retry and self.budget.retries >= MAX_RETRIES:
            return self._refuse(call_id, name, ToolError("RETRY_LIMIT", "the one allowed retry is already used"))
        if self.budget.tool_calls >= MAX_TOOL_CALLS:
            self._log("stop", call_id=call_id, reason="tool_budget", tool=name)
            raise StopRun("tool_budget")
        self.budget.tool_calls += 1
        if is_retry:
            self.budget.retries += 1
            self.budget.awaiting_retry.remove(sig)

        try:
            result = self._execute(name, spec, args)
        except ToolError as e:
            if e.retryable:
                e.retryable = self.budget.retries < MAX_RETRIES  # tell the model if a retry is still allowed
                if e.retryable:
                    self.budget.awaiting_retry.append(sig)
            self._log("tool_result", call_id=call_id, tool=name, ok=False, error=e.as_dict(), retry=is_retry)
            return {"ok": False, "error": e.as_dict()}
        except StopRun as stop:
            self._log("stop", call_id=call_id, reason=stop.reason, tool=name)
            raise
        self._log("tool_result", call_id=call_id, tool=name, ok=True, result=result, retry=is_retry)
        return {"ok": True, "result": result}

    def _execute(self, name, spec, args):
        """Run one validated call. Changing tools reuse a saved receipt for the same key instead of acting twice."""
        if not spec.mutating:
            self._fire_fault(name)  # read tools fail before running
            return spec.fn(self.store, self._ctx(), args)

        key = action_key(self.ticket_id, name, args)
        saved = self.store.data["receipts"].get(key)
        if saved is not None:
            result = {**saved, "replayed": True}
        else:
            if self._fault_hits(name) and self.budget.fault["error"] != "TIMEOUT":
                self._fire_fault(name)  # refusals like VERSION_CONFLICT happen before any write
            result = spec.fn(self.store, self._ctx(key), args)
            self._record(name, key, result)
        self._fire_fault(name)  # a timeout here means the action happened but its reply was lost
        return result

    def _record(self, name, key, result):
        """Remember the receipt under its key, attach it to the ticket, and save the store at once."""
        self.store.data["receipts"][key] = result
        self.store.ticket_state(self.ticket_id)["actions"].append({"type": name, "receipt_id": result["receipt_id"]})
        self.store.save()

    def _fault_hits(self, name):
        """Is an injected failure for this tool still pending?"""
        fault = self.budget.fault
        return bool(fault) and fault["tool"] == name and fault["remaining"] != 0

    def _fire_fault(self, name):
        """Raise the injected failure if one is pending for this tool, and count it down ('all' never runs out)."""
        if not self._fault_hits(name):
            return
        fault = self.budget.fault
        if fault["remaining"] > 0:
            fault["remaining"] -= 1
        raise ToolError(fault["error"], f"simulated {fault['error']} from {name}", retryable=fault["retryable"])

    def _ctx(self, key=None):
        """Context for a tool: this ticket and customer only, built by Python."""
        return Context(ticket_id=self.ticket_id, customer_id=self.customer_id, key=key, retriever=self.retriever)

    def _refuse(self, call_id, name, error):
        """Reject a call before it reaches a tool. Costs no tool budget."""
        self._log("tool_refused", call_id=call_id, tool=name, error=error.as_dict())
        return {"ok": False, "error": error.as_dict()}

    def _log(self, event, **fields):
        """Trace an event if tracing is on (tests may run without it)."""
        if self.trace:
            self.trace.log(event, **fields)

    def save_failed_safe(self, category, reason):
        """Python's own save when a run must stop: failed_safe with the reason and every receipt so far.
        Outside the model's budget, but through the same ticket_update checks."""
        state = self.store.ticket_state(self.ticket_id)
        receipts = [a["receipt_id"] for a in state["actions"] if not a["receipt_id"].startswith("AUD-")]
        result = TicketResult(ticket_id=self.ticket_id, category=Category(category), status=Status.FAILED_SAFE,
                              response=None, citations=[], receipt_ids=receipts,
                              unresolved_requirements=[f"staff recovery: {reason}"], stop_reason=reason)
        args = TicketUpdateArgs(result=result, expected_version=state["version"])
        try:
            saved = ticket_update(self.store, self._ctx(), args)
        except ToolError as e:  # e.g. already escalated: staff own it, leave the status alone
            self._log("failed_safe_not_saved", reason=reason, error=e.as_dict())
            return None
        self._record("ticket_update", f"{self.ticket_id}:failed_safe:{state['version']}", saved)
        self._log("failed_safe", reason=reason, receipt=saved)
        return saved
