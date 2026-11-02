"""Read actual fixtures into an isolated in-memory console; never write runtime data."""

import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from typing import Any
from uuid import uuid4

from .adapter import AdapterError
from .evaluation import build_eval_runs, build_retrieval_metrics, eval_summaries
from .grading import expected_outcome

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIMITS = {
    "model_calls": 4,
    "tool_calls": 6,
    "retries": 1,
    "schema_repairs": 1,
    "revisions": 1,
}
DEMO_TICKETS = {"timeout_retry": "T-0052", "repeated_call": "T-0029", "refund_gate": "T-0055"}
CARD_NUMBER = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

BUDGET_NOTICE = (
    "Scripted MOCK demo allowance: 8 lifetime tool calls. The real backend currently allows 6; "
    "the team must reconcile this before real integration. Resume preserves all counters."
)


class MockStop(Exception):
    """An explicit mock gate failure or exhausted counter ends work without further actions."""

    def __init__(self, code: str, message: str):
        """Carry a stable trace code and a useful public explanation."""
        super().__init__(message)
        self.code = code


def _new_budget() -> dict[str, int]:
    """Create independent cumulative counters for one ticket lifecycle."""
    return {key: 0 for key in DEFAULT_LIMITS}


def _redact(value: Any) -> Any:
    """Match real trace behavior by removing card-like strings at every nested level."""
    if isinstance(value, str):
        return CARD_NUMBER.sub("[card number removed]", value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    """Read a fixture without modifying the source or loading local secrets."""
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per nonempty fixture line."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _policy_from_file(path: Path, expected_id: str) -> dict[str, Any]:
    """Parse the existing simple policy markdown without adding a YAML dependency."""
    text = path.read_text(encoding="utf-8")
    title = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    metadata = dict(re.findall(r"^(id|version|effective_date):\s*([^\n]+)$", text, flags=re.MULTILINE))
    sections = {key: "" for key in ("S1", "S2", "S3", "S4")}
    chunks = re.split(r"^##\s+(S[1-4])\b[^\n]*\n", text, flags=re.MULTILINE)
    for index in range(1, len(chunks), 2):
        sections[chunks[index]] = chunks[index + 1].strip()
    return {
        "policy_id": metadata.get("id", expected_id).strip("\"'"),
        "title": title.group(1).strip() if title else expected_id,
        "version": metadata.get("version", "").strip("\"'"),
        "effective_date": metadata.get("effective_date", "").strip("\"'"),
        "sections": sections,
    }


class MockAdapter:
    """Isolated scripted UI simulations; no model, real-agent tools or runtime writes."""

    name = "mock"

    def __init__(self, data_dir: str | Path | None = None, policy_dir: str | Path | None = None,
                 step_delay: float = 0.65, tool_limit: int = 8):
        """Load source snapshots once; responses and working state use independent copies."""
        self._lock = RLock()
        if step_delay < 0 or tool_limit < 1:
            raise ValueError("step_delay must be nonnegative and tool_limit must be positive")
        self.step_delay = step_delay
        self.limits = {**DEFAULT_LIMITS, "tool_calls": tool_limit}
        self.data_dir = Path(data_dir) if data_dir is not None else REPO / "data" / "synthetic"
        self.policy_dir = Path(policy_dir) if policy_dir is not None else REPO / "policies"
        self._source = _read_json(self.data_dir / "store.json")
        self._labels = {label["ticket_id"]: label for label in _read_jsonl(self.data_dir / "labels.jsonl")}
        self._staged = {approval["approval_id"]: approval for approval in _read_json(self.data_dir / "demo_staged_approval.json")}
        self._tickets = {
            ticket["ticket_id"]: {key: value for key, value in ticket.items() if key != "failure_injection"}
            for ticket in _read_jsonl(self.data_dir / "tickets.jsonl")
        }
        self._splits = {
            ticket_id: split for split, ticket_ids in _read_json(self.data_dir / "splits.json").items()
            for ticket_id in ticket_ids
        }
        policy_ids = _read_json(self.data_dir / "policy_ids.json")
        self._policies = {
            policy_id: _policy_from_file(self.policy_dir / f"{policy_id}.md", policy_id)
            for policy_id in sorted(policy_ids)
        }
        self._store: dict[str, Any] = {}
        self._active: dict[str, str] | None = None
        self._eval_runs = build_eval_runs(self._labels, self._splits, self._policies, self._source["as_of"])
        self._retrieval_metrics = build_retrieval_metrics(self._labels, self._splits, self._policies)
        self.reset()

    def metadata(self) -> dict[str, Any]:
        """Disclose scripted mode, global run state and the separate mock/backend allowances."""
        with self._lock:
            return {
                "adapter": self.name,
                "as_of": self._store["as_of"],
                "running": self._active is not None,
                "active_ticket_id": self._active["ticket_id"] if self._active else None,
                "capabilities": {
                    "run": True, "resume": True, "approve": True,
                    "reset": True, "trace": True, "evaluation": True, "grader": True,
                },
                "limits": deepcopy(self.limits),
                "backend_limits": deepcopy(DEFAULT_LIMITS),
                "budget_notice": BUDGET_NOTICE if self.limits["tool_calls"] != DEFAULT_LIMITS["tool_calls"] else None,
            }

    def is_running(self) -> bool:
        """Read the same process-wide execution lock used by starting, approval and reset."""
        with self._lock:
            return self._active is not None

    def _summary(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """Compose an inbox row from observable state rather than grader labels."""
        state = self._store["ticket_state"][ticket["ticket_id"]]
        customer = self._customers[ticket["customer_id"]]
        result = state.get("last_result") or {}
        return {
            "ticket_id": ticket["ticket_id"], "subject": ticket["subject"],
            "customer_id": ticket["customer_id"], "customer_name": customer["name"],
            "channel": ticket["channel"], "priority": ticket["priority"],
            "created_at": ticket["created_at"], "status": state["status"],
            "version": state["version"], "team": state.get("team"),
            "category": result.get("category"), "split": self._splits[ticket["ticket_id"]],
            "running": bool(self._active and self._active["ticket_id"] == ticket["ticket_id"]),
        }

    def list_tickets(
        self, *, status: str | None = None, team: str | None = None,
        priority: str | None = None, channel: str | None = None,
        split: str | None = None, q: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter the public inbox; search is case-insensitive across ID, subject and customer."""
        filters = {"status": status, "team": team, "priority": priority, "channel": channel, "split": split}
        needle = q.strip().casefold() if q else ""
        with self._lock:
            rows = [self._summary(ticket) for ticket in self._tickets.values()]
            rows = [row for row in rows if all(value is None or row[key] == value for key, value in filters.items())]
            if needle:
                rows = [row for row in rows if any(
                    needle in row[key].casefold() for key in ("ticket_id", "subject", "customer_name")
                )]
            return deepcopy(sorted(rows, key=lambda row: row["ticket_id"]))

    def _order_view(self, order: dict[str, Any]) -> dict[str, Any]:
        """Enrich an owned order with its active approvals, payment evidence and balance."""
        order_id = order["order_id"]
        payments = [payment for payment in self._store["payments"] if payment["order_id"] == order_id]
        refunds = [refund for refund in self._store["refunds"] if refund["order_id"] == order_id]
        approvals = [
            {
                **approval,
                "used_by_refund": next((
                    refund["refund_id"] for refund in self._store["refunds"]
                    if refund.get("approval_id") == approval["approval_id"]
                ), None),
            }
            for approval in self._store["approvals"] if approval["order_id"] == order_id
        ]
        captured = sum(payment["amount_cents"] for payment in payments if payment["status"] == "captured")
        committed = sum(refund["amount_cents"] for refund in refunds if refund["status"] in {"issued", "settled"})
        return {
            **order, "payments": payments, "approvals": approvals, "refunds": refunds,
            "remaining_refundable_cents": captured - committed,
        }

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Return ticket details while keeping foreign or unknown orders inaccessible."""
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise AdapterError(404, f"Ticket {ticket_id!r} was not found.")
            state = self._store["ticket_state"][ticket_id]
            orders, inaccessible = [], []
            for order_id in ticket["order_ids"]:
                order = self._orders.get(order_id)
                if order is None or order["customer_id"] != ticket["customer_id"]:
                    inaccessible.append(order_id)
                else:
                    orders.append(self._order_view(order))
            return deepcopy({
                "ticket": ticket,
                "customer": self._customers[ticket["customer_id"]],
                "orders": orders,
                "orders_not_on_this_account": inaccessible,
                "state": {
                    "status": state["status"], "version": state["version"],
                    "team": state.get("team"), "actions": state.get("actions", []),
                    "last_result": state.get("last_result"),
                },
                "sent_messages": [message for message in self._store.get("outbox", []) if message["ticket_id"] == ticket_id],
                "as_of": self._store["as_of"],
                "running": bool(self._active and self._active["ticket_id"] == ticket_id),
                "execution": self._execution_view(ticket_id),
            })

    def list_policies(self) -> list[dict[str, Any]]:
        """Return actual policy versions rather than hard-coding a common version."""
        with self._lock:
            return [{key: policy[key] for key in ("policy_id", "title", "version")} for policy in self._policies.values()]

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        """Look up an indexed policy safely; arbitrary paths are never read."""
        with self._lock:
            policy = self._policies.get(policy_id)
            if policy is None:
                raise AdapterError(404, f"Policy {policy_id!r} was not found.")
            return deepcopy(policy)

    def reset(self) -> dict[str, int]:
        """Restore the private snapshot entirely in memory; source and runtime files stay untouched."""
        with self._lock:
            if self._active is not None:
                raise AdapterError(409, "A run is in progress; wait for it to finish before resetting.")
            self._store = deepcopy(self._source)
            self._store["outbox"] = []
            self._executions: dict[str, dict[str, Any]] = {}
            self._traces: dict[str, list[dict[str, Any]]] = {}
            self._receipts: dict[str, dict[str, Any]] = {}
            self._counters = {"RF": 4000, "MSG": 9000, "AUD": 5000, "CAN": 7000}
            for record in self._store["refunds"]:
                prefix, number = record["refund_id"].split("-", 1)
                self._counters[prefix] = max(self._counters.get(prefix, 0), int(number))
            for state in self._store["ticket_state"].values():
                for action in state.get("actions", []):
                    prefix, number = action["receipt_id"].split("-", 1)
                    self._counters[prefix] = max(self._counters.get(prefix, 0), int(number))
            self._customers = {customer["customer_id"]: customer for customer in self._store["customers"]}
            self._orders = {order["order_id"]: order for order in self._store["orders"]}
            return {"orders": len(self._store["orders"]), "approvals": len(self._store["approvals"])}

    def _require_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Centralize unknown-ticket handling before scheduling or reading progress."""
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            raise AdapterError(404, f"Ticket {ticket_id!r} was not found.")
        return ticket

    def _pending_refund(self, ticket_id: str) -> dict[str, Any] | None:
        """Use structured fixture arguments, never parse money out of English labels."""
        ticket = self._tickets[ticket_id]
        label = self._labels[ticket_id]
        args = label.get("after_resume", {}).get("expected_tool_args", {}).get("process_refund")
        args = args or label.get("expected_tool_args", {}).get("process_refund")
        if args:
            return {"order_id": args["order_id"], "amount_cents": args["cents"],
                    "approval_id": args.get("approval_id"), "reason": "Exact staff refund approval required"}
        staged = next((item for item in self._staged.values() if item["order_id"] in ticket["order_ids"]), None)
        if staged:
            return {"order_id": staged["order_id"], "amount_cents": staged["amount_cents"],
                    "approval_id": staged["approval_id"], "reason": staged["reason"]}
        owned = [self._orders[order_id] for order_id in ticket["order_ids"]
                 if order_id in self._orders and self._orders[order_id]["customer_id"] == ticket["customer_id"]]
        if not owned:
            return None
        reason = "; ".join(label.get("unresolved_requirements", [])) or "Staff must determine the refund remedy"
        return {"order_id": owned[0]["order_id"], "amount_cents": None, "approval_id": None, "reason": reason}

    def _resume_blocked_reason(self, ticket_id: str) -> str | None:
        """Only an exact, unused approval can satisfy this milestone's supported pause."""
        state = self._store["ticket_state"][ticket_id]
        if state["status"] == "awaiting_customer":
            return "Customer input is still required. Customer-reply simulation is not connected in this milestone."
        if state["status"] == "failed_safe":
            return "Staff recovery is required. Reset the mock workspace to replay this scenario."
        if state["status"] != "awaiting_approval":
            return "This ticket is not waiting for staff approval."
        if not self._labels[ticket_id].get("after_resume"):
            return "This pause needs a staff decision beyond the staged approval demo."
        pending = self._pending_refund(ticket_id)
        if not pending or not pending["approval_id"]:
            return "An exact staff refund approval is still required."
        approval = next((item for item in self._store["approvals"] if item["approval_id"] == pending["approval_id"]), None)
        if approval is None or approval["order_id"] != pending["order_id"] or approval["amount_cents"] != pending["amount_cents"]:
            return f"Activate staff approval {pending['approval_id']} for the exact order and amount first."
        if any(refund["approval_id"] == approval["approval_id"] for refund in self._store["refunds"]):
            return "This approval has already been consumed by a refund."
        return None

    def _execution_view(self, ticket_id: str) -> dict[str, Any]:
        """Supply authoritative lifetime counters so the client need not infer free refusals."""
        execution = self._executions.get(ticket_id)
        blocked = self._resume_blocked_reason(ticket_id)
        return {
            "budget": deepcopy(execution["budget"] if execution else _new_budget()),
            "limits": deepcopy(self.limits), "backend_limits": deepcopy(DEFAULT_LIMITS),
            "budget_notice": BUDGET_NOTICE if self.limits["tool_calls"] != DEFAULT_LIMITS["tool_calls"] else None,
            "active_run_id": self._active["run_id"] if self._active and self._active["ticket_id"] == ticket_id else None,
            "last_run_id": execution.get("last_run_id") if execution else None,
            "system": execution["system"] if execution else None,
            "scenario": execution.get("scenario", "standard") if execution else "standard",
            "last_mode": execution.get("last_mode") if execution else None,
            "can_run": self._active is None and execution is None,
            "run_blocked_reason": "Another ticket is running." if self._active else
                "This ticket already ran in this snapshot. Reset the workspace before comparing systems." if execution else None,
            "can_resume": self._active is None and blocked is None,
            "resume_blocked_reason": "Another ticket is running." if self._active else blocked,
            "pending_refund": self._pending_refund(ticket_id) if self._store["ticket_state"][ticket_id]["status"] == "awaiting_approval" else None,
        }

    def start_run(self, ticket_id: str, system: str, scenario: str = "standard") -> str:
        """Claim the global worker atomically before returning a nonblocking run identifier."""
        with self._lock:
            self._require_ticket(ticket_id)
            if system not in {"agent", "baseline"}:
                raise AdapterError(400, "system must be 'agent' or 'baseline'.")
            if scenario != "standard" and (scenario not in DEMO_TICKETS or DEMO_TICKETS[scenario] != ticket_id):
                raise AdapterError(400, "This demonstration is not available for the selected ticket.")
            if scenario != "standard" and system != "agent":
                raise AdapterError(400, "Forced demonstrations use the scripted agent. Choose standard mode for a baseline run.")
            if self._active is not None:
                raise AdapterError(409, "A run is already in progress. Wait for it to finish.")
            if ticket_id in self._executions:
                raise AdapterError(409, "This ticket already ran in this snapshot. Reset the workspace before another agent/baseline comparison.")
            self._executions[ticket_id] = {"system": system, "scenario": scenario, "budget": _new_budget(), "seen": {}}
            return self._schedule(ticket_id, "run")

    def start_resume(self, ticket_id: str) -> str:
        """Resume an approved ticket without replacing its saved budget or previous receipts."""
        with self._lock:
            self._require_ticket(ticket_id)
            if self._active is not None:
                raise AdapterError(409, "A run is already in progress. Wait for it to finish.")
            blocked = self._resume_blocked_reason(ticket_id)
            if blocked is not None:
                raise AdapterError(400, blocked)
            self._executions.setdefault(ticket_id, {"system": "agent", "budget": _new_budget()})
            return self._schedule(ticket_id, "resume")

    def _schedule(self, ticket_id: str, mode: str) -> str:
        """Start one daemon worker; its state is exclusively owned by this mock adapter."""
        run_id = f"{mode}-{uuid4().hex[:12]}"
        self._active = {"ticket_id": ticket_id, "run_id": run_id}
        self._executions[ticket_id]["last_run_id"] = run_id
        self._executions[ticket_id]["last_mode"] = mode
        Thread(target=self._run_worker, args=(ticket_id, run_id, mode), daemon=True,
               name=f"opus-mock-{run_id}").start()
        return run_id

    def get_trace(self, ticket_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Return append-only events after an event-count cursor, including budget snapshots."""
        with self._lock:
            self._require_ticket(ticket_id)
            if isinstance(since, bool) or not isinstance(since, int) or since < 0:
                raise AdapterError(400, "since must be a nonnegative event index.")
            return deepcopy(self._traces.get(ticket_id, [])[since:])

    def get_expected(self, ticket_id: str) -> dict[str, Any]:
        """Expose draft answers only through the explicit grader endpoint, with observed checks."""
        with self._lock:
            self._require_ticket(ticket_id)
            return expected_outcome(self._labels[ticket_id], self._store["ticket_state"][ticket_id],
                                    self._traces.get(ticket_id, []), self._executions.get(ticket_id),
                                    bool(self._active and self._active["ticket_id"] == ticket_id))

    def list_eval_runs(self) -> list[dict[str, Any]]:
        """List immutable illustrative snapshots, independent of interactive demo state."""
        return deepcopy(eval_summaries(self._eval_runs))

    def get_eval_run(self, run_id: str) -> dict[str, Any]:
        """Return one explicitly synthetic dashboard dataset or a descriptive 404."""
        if run_id not in self._eval_runs:
            raise AdapterError(404, "Evaluation snapshot was not found.")
        return deepcopy(self._eval_runs[run_id])

    def retrieval_metrics(self) -> dict[str, Any]:
        """Return mock ranking illustrations; no real retrieval measurement is claimed."""
        return deepcopy(self._retrieval_metrics)

    def list_approvals(self) -> dict[str, Any]:
        """Show active/staged approvals and structured waiting requirements for staff only."""
        with self._lock:
            waiting = [
                {**self._summary(ticket), "pending_refund": self._pending_refund(ticket_id),
                 "can_resume": self._execution_view(ticket_id)["can_resume"],
                 "resume_blocked_reason": self._execution_view(ticket_id)["resume_blocked_reason"],
                 "unresolved_requirements": (self._store["ticket_state"][ticket_id].get("last_result") or {}).get("unresolved_requirements", [])}
                for ticket_id, ticket in self._tickets.items()
                if self._store["ticket_state"][ticket_id]["status"] == "awaiting_approval"
            ]
            staged = [{**item, "active": any(active["approval_id"] == item["approval_id"] for active in self._store["approvals"]),
                       "used_by_refund": next((refund["refund_id"] for refund in self._store["refunds"]
                                               if refund["approval_id"] == item["approval_id"]), None),
                       "unblocks": [row["ticket_id"] for row in waiting if row["pending_refund"]
                                    and row["pending_refund"]["order_id"] == item["order_id"]
                                    and row["pending_refund"]["amount_cents"] == item["amount_cents"]]}
                      for item in self._staged.values()]
            return deepcopy({"waiting": waiting, "staged": staged})

    def approve(self, approval_id: str) -> dict[str, Any]:
        """Activate a known staff-created record once; the model has no approval-creation API."""
        with self._lock:
            staged = self._staged.get(approval_id)
            if staged is None:
                raise AdapterError(404, f"Approval {approval_id!r} is not a staged staff record.")
            active = next((item for item in self._store["approvals"] if item["approval_id"] == approval_id), None)
            if active is not None:
                return deepcopy({"approval": active, "already_active": True})
            if self._active is not None:
                raise AdapterError(409, "Wait for the active run before changing staff approvals.")
            self._store["approvals"].append(deepcopy(staged))
            for ticket_id, ticket in self._tickets.items():
                if staged["order_id"] in ticket["order_ids"]:
                    order = self._orders.get(staged["order_id"])
                    if order and order["customer_id"] == ticket["customer_id"]:
                        self._append_event(ticket_id, "staff", "staff_approval", approval=staged)
            return deepcopy({"approval": staged, "already_active": False})

    def _append_event(self, ticket_id: str, run_id: str, event: str, **fields: Any) -> None:
        """Append a detached event under lock; every counter is cumulative over resumes."""
        with self._lock:
            execution = self._executions.get(ticket_id)
            self._traces.setdefault(ticket_id, []).append(deepcopy(_redact({
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "ticket_id": ticket_id, "run_id": run_id, "event": event, "mock": True,
                "budget": execution["budget"] if execution else _new_budget(),
                "limits": self.limits, **fields,
            })))

    def _event(self, ticket_id: str, run_id: str, event: str, **fields: Any) -> None:
        """Space scripted events so the local polling UI exercises real progress updates."""
        if self.step_delay:
            time.sleep(self.step_delay)
        self._append_event(ticket_id, run_id, event, **fields)

    def _model(self, ticket_id: str, run_id: str, tools: list[str], text: str) -> None:
        """Count a scripted model decision without contacting a model service."""
        with self._lock:
            budget = self._executions[ticket_id]["budget"]
            if budget["model_calls"] >= self.limits["model_calls"]:
                raise MockStop("model_budget", "The lifetime model-call budget is exhausted.")
            budget["model_calls"] += 1
            number = budget["model_calls"]
        self._event(ticket_id, run_id, "model_call", n=number, tool_calls=tools, text=text,
                    tokens_in=0, tokens_out=0, latency_ms=0)

    def _call(self, ticket_id: str, run_id: str, tool: str, args: dict[str, Any], operation,
              retry: bool = False, timeout_after_write: bool = False) -> dict[str, Any]:
        """Enforce budget before each tool and preserve receipts for repeated identical writes."""
        with self._lock:
            execution = self._executions[ticket_id]
            budget = execution["budget"]
            signature = f"{tool}:{json.dumps(args, sort_keys=True)}"
            seen = execution.setdefault("seen", {})
            seen[signature] = seen.get(signature, 0) + 1
            execution["call_sequence"] = execution.get("call_sequence", 0) + 1
            call_id = f"{run_id}-t{execution['call_sequence']}"
        self._event(ticket_id, run_id, "tool_request", call_id=call_id, tool=tool, args=json.dumps(args, sort_keys=True))
        with self._lock:
            if seen[signature] >= 3:
                self._append_event(ticket_id, run_id, "stop", call_id=call_id, reason="repeated_call", tool=tool)
                raise MockStop("repeated_call", "A third identical tool request was stopped before execution.")
            if retry and budget["retries"] >= self.limits["retries"]:
                self._append_event(ticket_id, run_id, "tool_refused", call_id=call_id, tool=tool,
                                   error={"code": "RETRY_LIMIT", "message": "The allowed retry has already been used.", "retryable": False})
                raise MockStop("RETRY_LIMIT", "The allowed retry has already been used.")
            if budget["tool_calls"] >= self.limits["tool_calls"]:
                self._append_event(ticket_id, run_id, "stop", call_id=call_id, reason="tool_budget", tool=tool)
                raise MockStop("tool_budget", "The lifetime tool-call budget is exhausted.")
            budget["tool_calls"] += 1
            if retry:
                budget["retries"] += 1
        try:
            with self._lock:
                key = f"{ticket_id}:{tool}:{json.dumps(args, sort_keys=True)}"
                if key in self._receipts:
                    result = {**self._receipts[key], "replayed": True}
                else:
                    result = operation()
                    if "receipt_id" in result:
                        self._receipts[key] = deepcopy(result)
                        self._store["ticket_state"][ticket_id].setdefault("actions", []).append({"type": tool, "receipt_id": result["receipt_id"]})
        except MockStop as error:
            self._event(ticket_id, run_id, "tool_result", call_id=call_id, tool=tool, ok=False,
                        error={"code": error.code, "message": str(error), "retryable": False}, retry=retry)
            raise
        if timeout_after_write:
            self._event(ticket_id, run_id, "tool_result", call_id=call_id, tool=tool, ok=False,
                        error={"code": "TIMEOUT", "message": "Simulated reply loss after the write; retry with the same operation key.", "retryable": True}, retry=False)
            raise MockStop("TIMEOUT", "The write succeeded but its reply timed out.")
        self._event(ticket_id, run_id, "tool_result", call_id=call_id, tool=tool, ok=True, result=result, retry=retry)
        return result

    def _next_id(self, prefix: str) -> str:
        """Allocate IDs above the seed ledger, never overwrite an existing receipt."""
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]}"

    def _policy_lookup(self, ticket_id: str) -> dict[str, Any]:
        """Return actual policy text inside a clearly scripted retrieval event."""
        ids = self._labels[ticket_id]["policy_ids"][:3]
        method = "bm25" if ticket_id == "T-0002" else "embeddings"
        passages = []
        remaining = 4000
        for policy_id in ids:
            policy = self._policies[policy_id]
            text = f"{policy_id} v{policy['version']}: {policy['title']}\n" + "\n".join(policy["sections"].values())
            text = text[:remaining]
            remaining -= len(text)
            passages.append({"policy_id": policy_id, "version": policy["version"], "title": policy["title"],
                             "score": 0.61, "best_section": "Rules", "text": text})
        return {"method": method, "fallback_reason": "Simulated embedding outage" if method == "bm25" else None,
                "best_score": 0.61, "weak_evidence": False, "chars": 4000 - remaining, "passages": passages}

    def _owned_order(self, ticket_id: str, order_id: str) -> dict[str, Any]:
        """Mock money and cancellation writes enforce ownership before any mutation."""
        order = self._orders.get(order_id)
        ticket = self._tickets[ticket_id]
        if order is None or order["customer_id"] != ticket["customer_id"] or order_id not in ticket["order_ids"]:
            raise MockStop("ORDER_NOT_FOUND", "This order is not accessible on the customer's account.")
        return order

    def _refund(self, ticket_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Issue one simulated refund only with owned records and an exact unused approval."""
        order = self._owned_order(ticket_id, args["order_id"])
        cents = args["cents"]
        approval = next((item for item in self._store["approvals"] if item["approval_id"] == args["approval_id"]), None)
        if isinstance(cents, bool) or not isinstance(cents, int) or cents <= 0:
            raise MockStop("INVALID_ARGS", "Refunds require positive integer cents.")
        if approval is None or approval["order_id"] != args["order_id"] or approval["amount_cents"] != cents:
            raise MockStop("NO_MATCHING_APPROVAL", "No staff approval matches this exact order and amount.")
        if any(refund["approval_id"] == approval["approval_id"] for refund in self._store["refunds"]):
            raise MockStop("APPROVAL_ALREADY_USED", "This approval has already been consumed.")
        view = self._order_view(order)
        if cents > view["remaining_refundable_cents"]:
            raise MockStop("EXCEEDS_REMAINING_BALANCE", "The refund exceeds the remaining captured balance.")
        label = self._labels[ticket_id]
        allowed = label.get("after_resume", {}).get("expected_actions", []) + label.get("expected_actions", [])
        if "process_refund" not in allowed:
            raise MockStop("NOT_ELIGIBLE", "This scripted scenario does not establish a refund remedy.")
        refund = {"refund_id": self._next_id("RF"), "order_id": order["order_id"], "amount_cents": cents,
                  "approval_id": approval["approval_id"], "created_at": self._store["as_of"], "status": "issued"}
        self._store["refunds"].append(refund)
        if self._order_view(order)["remaining_refundable_cents"] == 0:
            order["status"] = "refunded"
        return {**refund, "receipt_id": refund["refund_id"], "basis": "scripted_fixture_eligibility"}

    def _cancel(self, ticket_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """Reverse an owned unshipped mock order and leave a cancellation receipt."""
        order = self._owned_order(ticket_id, args["order_id"])
        if order["status"] != "processing" or order.get("shipment") is not None:
            raise MockStop("ALREADY_SHIPPED", "Only an unshipped processing order can be cancelled.")
        order["status"] = "cancelled"
        for payment in self._store["payments"]:
            if payment["order_id"] == order["order_id"] and payment["status"] == "captured":
                payment["status"] = "reversed"
        return {"receipt_id": self._next_id("CAN"), "order_id": order["order_id"], "status": "cancelled"}

    def _send_message(self, ticket_id: str, body: str, citations: list[str]) -> dict[str, Any]:
        """Append an outbox entry to memory addressed only to the stored simulated customer."""
        if CARD_NUMBER.search(body):
            raise MockStop("CARD_NUMBER_IN_BODY", "Remove the full card number before sending a reply.")
        if not citations or not set(citations) <= self._policies.keys():
            raise MockStop("UNKNOWN_POLICY", "A sent reply requires known policy citations.")
        ticket = self._tickets[ticket_id]
        message_id = self._next_id("MSG")
        to = self._customers[ticket["customer_id"]]["email"]
        self._store["outbox"].append({"message_id": message_id, "ticket_id": ticket_id, "to": to,
                                      "body": body, "citations": citations, "sent_at": self._store["as_of"]})
        return {"receipt_id": message_id, "to": to, "status": "sent"}

    def _save_result(self, ticket_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Save only receipt-backed results and leave an audit receipt for the state change."""
        state = self._store["ticket_state"][ticket_id]
        own = {action["receipt_id"] for action in state.get("actions", [])}
        if not set(result["receipt_ids"]) <= own:
            raise MockStop("UNKNOWN_RECEIPT", "A result referenced an unknown action receipt.")
        if result["status"] in {"resolved", "closed"}:
            if result["unresolved_requirements"] or not any(receipt.startswith("MSG-") for receipt in result["receipt_ids"]):
                raise MockStop("INCOMPLETE", "Completion requires a sent message and no outstanding requirements.")
        state.update(status=result["status"], version=state["version"] + 1,
                     team=self._labels[ticket_id]["team"], last_result=deepcopy(result))
        return {"receipt_id": self._next_id("AUD"), "status": state["status"], "version": state["version"], "team": state["team"]}

    def _result(self, ticket_id: str, status: str, response: str | None, stop_reason: str,
                requirements: list[str], citations: list[str]) -> dict[str, Any]:
        """Compose a strict-shaped result from actual mock receipts, not fictional action claims."""
        return {"ticket_id": ticket_id, "category": self._labels[ticket_id]["category"], "status": status,
                "response": response, "citations": citations,
                "receipt_ids": [action["receipt_id"] for action in self._store["ticket_state"][ticket_id].get("actions", [])
                                if not action["receipt_id"].startswith("AUD-")],
                "unresolved_requirements": requirements, "stop_reason": stop_reason}

    def _run_worker(self, ticket_id: str, run_id: str, mode: str) -> None:
        """Execute a bounded scripted UI run and always release the process-wide worker."""
        try:
            self._script(ticket_id, run_id, mode)
        except MockStop as error:
            self._failed_safe(ticket_id, run_id, error.code, str(error))
        except Exception:
            self._failed_safe(ticket_id, run_id, "MOCK_RUN_ERROR", "The scripted mock run stopped unexpectedly.")
        finally:
            with self._lock:
                self._active = None

    def _failed_safe(self, ticket_id: str, run_id: str, reason: str, message: str) -> None:
        """Save recovery information outside the exhausted model/tool budget, as Python does."""
        with self._lock:
            result = self._result(ticket_id, "failed_safe", None, reason, [message], [])
            receipt = self._save_result(ticket_id, result)
            self._store["ticket_state"][ticket_id].setdefault("actions", []).append({"type": "ticket_update", "receipt_id": receipt["receipt_id"]})
        self._event(ticket_id, run_id, "failed_safe", reason=reason, receipt=receipt)
        self._event(ticket_id, run_id, "run_finished", status="failed_safe", result=result)

    def _script(self, ticket_id: str, run_id: str, mode: str) -> None:
        """Drive the approval demo and ordinary fixture-based outcomes with real-shaped events."""
        execution = self._executions[ticket_id]
        system = execution["system"]
        label = self._labels[ticket_id]
        citations = label["policy_ids"][:3]
        scenario = execution.get("scenario", "standard")
        self._event(ticket_id, run_id, "run_started", mode=mode, system=system, model="scripted-mock", scenario=scenario,
                    forced_test=scenario in {"repeated_call", "refund_gate"})
        if mode == "run" and scenario != "standard":
            self._demo_script(ticket_id, run_id, scenario)
            return
        if mode == "resume":
            if system == "agent":
                self._model(ticket_id, run_id, ["ticket_get", "process_refund", "send_message", "ticket_update"], "Recheck the staff approval and complete the saved refund requirement.")
            self._call(ticket_id, run_id, "ticket_get", {"ticket_id": ticket_id}, lambda: self.get_ticket(ticket_id))
            if system == "baseline":
                self._call(ticket_id, run_id, "kb_lookup", {"query": self._tickets[ticket_id]["subject"], "k": min(3, len(citations)) or 1}, lambda: self._policy_lookup(ticket_id))
                self._model(ticket_id, run_id, ["process_refund", "send_message", "ticket_update"], "Make one scripted single-pass decision after the one retrieval.")
            args = label["after_resume"]["expected_tool_args"]["process_refund"]
            refunded = self._call(ticket_id, run_id, "process_refund", args, lambda: self._refund(ticket_id, args))
            response = f"Your {self._money(args['cents'])} refund has been issued to the original payment method. Refund receipt: {refunded['receipt_id']}."
            status, requirements, stop_reason = "resolved", [], "refund_completed"
        else:
            if system == "agent":
                self._model(ticket_id, run_id, ["ticket_get"], "Read the ticket and only the customer's linked records.")
            self._call(ticket_id, run_id, "ticket_get", {"ticket_id": ticket_id}, lambda: self.get_ticket(ticket_id))
            if system == "agent":
                self._model(ticket_id, run_id, ["kb_lookup"], "Retrieve the policy evidence before choosing the next action.")
            self._call(ticket_id, run_id, "kb_lookup", {"query": self._tickets[ticket_id]["subject"], "k": min(3, len(citations)) or 1}, lambda: self._policy_lookup(ticket_id))
            planned_tools = [tool for tool in ("process_refund", "cancel_order")
                             if tool in label.get("expected_actions", []) and label.get("expected_tool_args", {}).get(tool)]
            pending = self._pending_refund(ticket_id)
            if label.get("after_resume") and pending and any(
                approval["approval_id"] == pending["approval_id"] for approval in self._store["approvals"]
            ):
                planned_tools.append("process_refund")
            if label["final_status"] != "failed_safe":
                planned_tools.append("send_message")
            planned_tools.append("ticket_update")
            self._model(ticket_id, run_id, planned_tools, "Choose the scripted fixture outcome and keep outstanding requirements explicit.")
            status = label["final_status"]
            requirements = label.get("unresolved_requirements", [])
            stop_reason = "awaiting_refund_approval" if status == "awaiting_approval" else f"scripted_{status}"
            if ticket_id == "T-0039":
                response = "We've received your Lenovo ThinkPad return. Your $1,149.00 refund is waiting on staff approval. No refund has been issued yet."
            else:
                response = ". ".join(label.get("response_facts", [])) or "Your request needs staff review before we can continue."
                response = response[0].upper() + response[1:]
                if not response.endswith((".", "?", "!")):
                    response += "."
            if status == "awaiting_approval" and label.get("after_resume"):
                # Staff may approve before a first run; never pretend a present approval is missing.
                pending = self._pending_refund(ticket_id)
                active = next((approval for approval in self._store["approvals"]
                               if pending and approval["approval_id"] == pending["approval_id"]
                               and approval["order_id"] == pending["order_id"]
                               and approval["amount_cents"] == pending["amount_cents"]), None)
                if active and not any(refund["approval_id"] == active["approval_id"] for refund in self._store["refunds"]):
                    args = label["after_resume"]["expected_tool_args"]["process_refund"]
                    refunded = self._call(ticket_id, run_id, "process_refund", args, lambda: self._refund(ticket_id, args))
                    response = f"Your {self._money(args['cents'])} refund has been issued to the original payment method. Refund receipt: {refunded['receipt_id']}."
                    status, requirements, stop_reason = "resolved", [], "refund_completed"
            for tool in ("process_refund", "cancel_order"):
                args = label.get("expected_tool_args", {}).get(tool)
                if args and tool in label.get("expected_actions", []):
                    operation = self._refund if tool == "process_refund" else self._cancel
                    self._call(ticket_id, run_id, tool, args, lambda operation=operation, args=args: operation(ticket_id, args))
        if status != "failed_safe":
            self._call(ticket_id, run_id, "send_message", {"ticket_id": ticket_id, "body": response, "citations": citations},
                       lambda: self._send_message(ticket_id, response, citations))
        result = self._result(ticket_id, status, response if status != "failed_safe" else None,
                              stop_reason, requirements, citations)
        self._call(ticket_id, run_id, "ticket_update", {"result": result, "expected_version": self._store["ticket_state"][ticket_id]["version"]},
                   lambda: self._save_result(ticket_id, result))
        self._event(ticket_id, run_id, "run_finished", status=status, result=result)

    @staticmethod
    def _money(cents: int) -> str:
        """Format human-readable mock reply amounts while keeping wire amounts in cents."""
        return f"${cents / 100:,.2f}"

    def _demo_script(self, ticket_id: str, run_id: str, scenario: str) -> None:
        """Exercise recovery and gates explicitly, without presenting a forced call as agent correctness."""
        label = self._labels[ticket_id]
        citations = label["policy_ids"][:3]
        self._model(ticket_id, run_id, ["ticket_get"], "Read the owned ticket records for this scripted demonstration.")
        args = {"ticket_id": ticket_id}
        self._call(ticket_id, run_id, "ticket_get", args, lambda: self.get_ticket(ticket_id))
        if scenario == "repeated_call":
            self._event(ticket_id, run_id, "demo_notice", text="Forced recovery test: repeat the same request three times. This is not a normal agent trajectory.")
            self._call(ticket_id, run_id, "ticket_get", args, lambda: self.get_ticket(ticket_id))
            self._call(ticket_id, run_id, "ticket_get", args, lambda: self.get_ticket(ticket_id))
            return
        self._model(ticket_id, run_id, ["kb_lookup"], "Find the policy evidence for the scripted case.")
        self._call(ticket_id, run_id, "kb_lookup", {"query": self._tickets[ticket_id]["subject"], "k": min(3, len(citations)) or 1}, lambda: self._policy_lookup(ticket_id))
        self._model(ticket_id, run_id, ["send_message", "ticket_update"], "Demonstrate recovery or a Python permission gate, then save the supported result.")
        if scenario == "refund_gate":
            self._event(ticket_id, run_id, "demo_notice", text="Forced safety test: deliberately submit an invalid approval to the Python gate. A correct agent should not attempt this refund.")
            refund_args = {"order_id": "O-60108", "cents": 34900, "approval_id": "APR-9999"}
            try:
                self._call(ticket_id, run_id, "process_refund", refund_args, lambda: self._refund(ticket_id, refund_args))
            except MockStop as error:
                if error.code != "NO_MATCHING_APPROVAL":
                    raise
            response = "Please return the complete item through the store return process and retain tracking. No refund has been issued."
            status, requirements, stop_reason = "awaiting_customer", label["unresolved_requirements"], "forced_refund_gate_test"
        else:
            response = ". ".join(label["response_facts"]).capitalize() + "."
            status, requirements, stop_reason = "resolved", [], "timeout_recovered"
        message_args = {"ticket_id": ticket_id, "body": response, "citations": citations}
        operation = lambda: self._send_message(ticket_id, response, citations)
        if scenario == "timeout_retry":
            try:
                self._call(ticket_id, run_id, "send_message", message_args, operation, timeout_after_write=True)
            except MockStop as error:
                if error.code != "TIMEOUT":
                    raise
            self._call(ticket_id, run_id, "send_message", message_args, operation, retry=True)
        else:
            self._call(ticket_id, run_id, "send_message", message_args, operation)
        result = self._result(ticket_id, status, response, stop_reason, requirements, citations)
        self._call(ticket_id, run_id, "ticket_update", {"result": result, "expected_version": self._store["ticket_state"][ticket_id]["version"]}, lambda: self._save_result(ticket_id, result))
        self._event(ticket_id, run_id, "run_finished", status=status, result=result)
