"""The real backend behind the console: the agent's store, tools, staff approvals, traces, agent and baseline.

Runs call the model (through Portkey, key read server-side from .env) in one background thread at a time.
Everything else reads the working store in runtime/ on each request, so the UI always shows what the tools
really wrote. Money and email are still simulated: refunds and replies only exist in runtime/store.json.
"""

import json
import re
from copy import deepcopy
from pathlib import Path
from threading import RLock, Thread
from typing import Any
from uuid import uuid4

from agent import agent as agent_loop
from agent import baseline
from agent.config import (CHAT_MODEL, MAX_MODEL_CALLS, MAX_RETRIES, MAX_REVISIONS, MAX_SCHEMA_REPAIRS,
                          MAX_TOOL_CALLS, RUNTIME)
from agent.dispatcher import Dispatcher
from agent.eval_run import RESULTS
from agent.progress import load_progress
from agent.schemas import Category
from agent.staff import approve as staff_approve
from agent.staff import staged_approvals
from agent.store import Store, load_jsonl
from agent.tools import _order_view
from agent.trace import Trace

from .adapter import AdapterError
from .evaluation import eval_summaries, load_measured
from .grading import expected_outcome

LIMITS = {"model_calls": MAX_MODEL_CALLS, "tool_calls": MAX_TOOL_CALLS, "retries": MAX_RETRIES,
          "schema_repairs": MAX_SCHEMA_REPAIRS, "revisions": MAX_REVISIONS}
APPROVAL_NEEDED = re.compile(r"staff approval for (\d+) cents", re.I)
NO_RETRIEVAL = {"measured": False, "queries": 0, "methods": [],
                "provenance": "No retrieval measurement yet: run python -m agent.eval_retrieval --save."}


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


def _default_llm():
    """The course model through Portkey; imported lazily so the console starts even without a key."""
    from agent.llm import OpenAILLM
    return OpenAILLM()


def _default_retriever():
    """Embeddings with BM25 fallback; BM25 only if the embedding index can't be built (e.g. offline)."""
    from agent.llm import embed
    from agent.retrieval import Retriever
    try:
        return Retriever(embed_fn=embed)
    except Exception:
        return Retriever(embed_fn=None)


class RealAdapter:
    """Console access to the real working store and real agent/baseline runs."""

    name = "real"

    def __init__(self, runtime: str | Path = RUNTIME, llm_factory=_default_llm, retriever_factory=_default_retriever,
                 results: str | Path = RESULTS):
        """Open (or create) the working store; nothing calls the model until a run starts."""
        self.root = Path(runtime)
        if not (self.root / "store.json").exists():
            Store.reset(self.root)
        self._lock = RLock()
        self._active: dict[str, str] | None = None
        self._llm_factory, self._retriever_factory = llm_factory, retriever_factory
        self._llm = self._retriever = None
        self._store = Store(self.root)  # read side: records reloaded from disk on every request
        data_dir = Path(agent_loop.__file__).resolve().parents[1] / "data" / "synthetic"
        self._labels = {label["ticket_id"]: label for label in load_jsonl(data_dir / "labels.jsonl")}
        self._splits = {tid: split for split, ids in json.loads((data_dir / "splits.json").read_text()).items()
                        for tid in ids}
        policy_dir = data_dir.parents[1] / "policies"
        self._policies = {pid: _policy_from_file(policy_dir / f"{pid}.md", pid) for pid in sorted(self._store.policy_ids)}
        self._results = Path(results)

    # --- reading ---

    def _fresh(self) -> Store:
        """The read-side store with records as they are on disk right now."""
        with self._lock:
            self._store.data = json.loads(self._store.path.read_text())
            return self._store

    def _require_ticket(self, ticket_id: str) -> dict[str, Any]:
        """The public ticket (no injected failures) or a 404."""
        if ticket_id not in self._store.tickets:
            raise AdapterError(404, f"Ticket {ticket_id!r} was not found.")
        return self._store.ticket_for_agent(ticket_id)

    def _running(self, ticket_id: str | None = None) -> bool:
        """Is any run (or this ticket's run) in progress?"""
        return self._active is not None and (ticket_id is None or self._active["ticket_id"] == ticket_id)

    def metadata(self) -> dict[str, Any]:
        """Real mode, store date, global run state and the backend's limits."""
        with self._lock:
            return {"adapter": self.name, "as_of": self._fresh().as_of, "running": self._running(),
                    "active_ticket_id": self._active["ticket_id"] if self._active else None, "model": CHAT_MODEL,
                    "capabilities": {"run": True, "resume": True, "approve": True, "reset": True, "trace": True,
                                     "evaluation": True, "grader": True},
                    "limits": dict(LIMITS)}

    def is_running(self) -> bool:
        """Whether the one background worker is busy."""
        with self._lock:
            return self._running()

    def _summary(self, store: Store, ticket_id: str) -> dict[str, Any]:
        """One inbox row from the saved ticket state."""
        ticket, state = store.tickets[ticket_id], store.ticket_state(ticket_id)
        return {"ticket_id": ticket_id, "subject": ticket["subject"], "customer_id": ticket["customer_id"],
                "customer_name": store.customer(ticket["customer_id"])["name"], "channel": ticket["channel"],
                "priority": ticket["priority"], "created_at": ticket["created_at"], "status": state["status"],
                "version": state["version"], "team": state.get("team"),
                "category": (state.get("last_result") or {}).get("category"), "split": self._splits[ticket_id],
                "running": self._running(ticket_id)}

    def list_tickets(self, *, status: str | None = None, team: str | None = None, priority: str | None = None,
                     channel: str | None = None, split: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
        """Filtered inbox; search matches ticket ID, subject and customer name."""
        filters = {"status": status, "team": team, "priority": priority, "channel": channel, "split": split}
        needle = q.strip().casefold() if q else ""
        with self._lock:
            store = self._fresh()
            rows = [self._summary(store, tid) for tid in sorted(store.tickets)]
        rows = [row for row in rows if all(value is None or row[key] == value for key, value in filters.items())]
        if needle:
            rows = [row for row in rows if any(needle in row[key].casefold()
                                               for key in ("ticket_id", "subject", "customer_name"))]
        return rows

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        """Ticket, the customer's own orders (same view the agent gets), saved state, outbox and run controls."""
        with self._lock:
            ticket = deepcopy(self._require_ticket(ticket_id))  # a caller editing the view can't change ours
            store = self._fresh()
            state = store.ticket_state(ticket_id)
            orders, inaccessible = [], []
            for order_id in ticket["order_ids"]:
                order = store.order(order_id)
                if order is None or order["customer_id"] != ticket["customer_id"]:
                    inaccessible.append(order_id)
                else:
                    orders.append(_order_view(store, order))
            return {"ticket": ticket, "customer": store.customer(ticket["customer_id"]), "orders": orders,
                    "orders_not_on_this_account": inaccessible,
                    "state": {"status": state["status"], "version": state["version"], "team": state.get("team"),
                              "actions": state["actions"], "last_result": state.get("last_result")},
                    "sent_messages": [m for m in store.data["outbox"] if m["ticket_id"] == ticket_id],
                    "as_of": store.as_of, "running": self._running(ticket_id),
                    "execution": self._execution_view(store, ticket_id)}

    def _trace_events(self, ticket_id: str) -> list[dict[str, Any]]:
        """All trace events for a ticket, numbering baseline model calls the way the viewer expects."""
        path = self.root / "traces" / f"{ticket_id}.jsonl"
        events = load_jsonl(path) if path.exists() else []
        counts: dict[str, int] = {}
        for event in events:
            if event["event"] == "model_call" and "n" not in event:
                counts[event["run_id"]] = counts.get(event["run_id"], 0) + 1
                event["n"] = counts[event["run_id"]]
        return events

    def _last_run(self, ticket_id: str) -> dict[str, Any] | None:
        """Latest execution identity, including progress charged before a trace could be written."""
        last = next((e for e in reversed(self._trace_events(ticket_id)) if e["event"] == "run_started"), None)
        progress = load_progress(self._fresh(), ticket_id)
        if progress and progress.get("run_id") and (last is None or progress["run_id"] != last["run_id"]):
            return {"run_id": progress["run_id"], "system": progress["system"], "mode": progress["mode"]}
        return last

    def _budget(self, store: Store, ticket_id: str, last: dict[str, Any] | None) -> dict[str, int]:
        """Live lifetime counters saved with action receipts; legacy runs fall back to their evidence."""
        progress = load_progress(store, ticket_id, last.get("system") if last else None)
        if progress:
            return {key: progress["budget"].get(key, 0) for key in LIMITS}
        checkpoint = agent_loop.load_checkpoint(store, ticket_id)
        events = self._trace_events(ticket_id) if last else []
        recorded = {"model_calls": sum(e["event"] == "model_call" for e in events),
                "tool_calls": sum(e["event"] == "tool_result" for e in events),
                "retries": sum(e["event"] == "tool_result" and bool(e.get("retry")) for e in events),
                "schema_repairs": sum(e["event"] == "schema_repair" for e in events),
                "revisions": sum(e["event"] == "revision" for e in events)}
        saved = checkpoint.get("budget", {}) if last and last.get("system") == "agent" and checkpoint else {}
        return {key: max(recorded[key], saved.get(key, 0)) for key in LIMITS}

    def _pending_refund(self, store: Store, ticket_id: str) -> dict[str, Any] | None:
        """The refund a paused ticket waits for, read from the agent's saved requirement (never from labels)."""
        if store.ticket_state(ticket_id)["status"] != "awaiting_approval":
            return None
        ticket = store.tickets[ticket_id]
        requirements = (store.ticket_state(ticket_id).get("last_result") or {}).get("unresolved_requirements", [])
        match = next((APPROVAL_NEEDED.search(r) for r in requirements if APPROVAL_NEEDED.search(r)), None)
        cents = int(match.group(1)) if match else None
        owned = [oid for oid in ticket["order_ids"]
                 if store.order(oid) and store.order(oid)["customer_id"] == ticket["customer_id"]]
        if not owned:
            return None
        mentioned = set(re.findall(r"\bO-\d+\b", " ".join(requirements))) & set(owned)
        if len(mentioned) == 1:
            order_id = mentioned.pop()
        elif len(owned) == 1:
            order_id = owned[0]
        else:
            # Multiple owned orders need an explicit target, not a guess based on available approvals.
            return None
        staged = next((a for a in staged_approvals().values()
                       if a["order_id"] == order_id and cents is not None and a["amount_cents"] == cents), None)
        return {"order_id": order_id, "amount_cents": cents, "approval_id": staged["approval_id"] if staged else None,
                "reason": "; ".join(requirements) or "Staff must decide the refund"}

    def _resume_blocked_reason(self, store: Store, ticket_id: str, last: dict[str, Any] | None) -> str | None:
        """Why Resume is unavailable, or None. Blocks resumes that would only spend budget without new input."""
        status = store.ticket_state(ticket_id)["status"]
        progress = load_progress(store, ticket_id, last.get("system") if last else None)
        interrupted_start = status == "new" and bool(progress and progress["system"] == "agent" and progress.get("in_progress"))
        if status not in agent_loop.RESUMABLE and not interrupted_start:
            return "This ticket is not paused."
        if last is None:
            return "Run the agent first; this ticket's saved state was not created by a run."
        if last.get("system") == "agent" and agent_loop.load_checkpoint(store, ticket_id) is None:
            return "No saved agent checkpoint for this ticket. Reset and run it again."
        if status == "awaiting_customer":
            return "Waiting for the customer to reply; customer replies are not simulated in this console."
        minimum_tools = 3  # re-read current records, send a reply, save the outcome
        if status == "awaiting_approval":
            pending = self._pending_refund(store, ticket_id)
            if pending is None or pending["amount_cents"] is None:
                return "Staff must establish the exact refund order and amount before this ticket can resume."
            oid, cents = pending["order_id"], pending["amount_cents"]
            progress = load_progress(store, ticket_id, last.get("system"))
            own_receipts = {action["receipt_id"] for action in store.ticket_state(ticket_id)["actions"]}
            committed = bool(progress and progress.get("in_progress") and any(
                refund["amount_cents"] == cents and refund["status"] in {"issued", "settled"}
                and refund["refund_id"] in own_receipts for refund in store.refunds(oid)))
            usable = any(a["amount_cents"] == cents and not store.refunds(approval_id=a["approval_id"])
                         for a in store.approvals(oid))
            if not usable and not committed:
                return "No active, unused staff approval for this exact order and amount yet. Approve it on the Approvals page."
            minimum_tools += (0 if committed else 1) + (1 if last.get("system") == "baseline" else 0)
        if last.get("system") == "baseline" and load_progress(store, ticket_id, "baseline") is None:
            return "This older baseline run has no saved lifetime budget; it needs staff recovery."
        budget = self._budget(store, ticket_id, last)
        minimum_models = 2 if last.get("system") == "agent" else 1
        if MAX_MODEL_CALLS - budget["model_calls"] < minimum_models or MAX_TOOL_CALLS - budget["tool_calls"] < minimum_tools:
            return "This ticket's run budget is used up; it needs staff recovery."
        return None

    def _execution_view(self, store: Store, ticket_id: str) -> dict[str, Any]:
        """Counters, limits and why Run or Resume is unavailable, for the run panel and trace meters."""
        last = self._last_run(ticket_id)
        blocked = self._resume_blocked_reason(store, ticket_id, last)
        busy = "Another ticket is running." if self._active else None
        return {"budget": self._budget(store, ticket_id, last), "limits": dict(LIMITS),
                "active_run_id": self._active["run_id"] if self._running(ticket_id) else None,
                "last_run_id": last["run_id"] if last else None, "system": last.get("system") if last else None,
                "last_mode": last.get("mode") if last else None,
                "can_run": self._active is None and last is None,
                "run_blocked_reason": busy or ("This ticket already ran. Reset the workspace to run it again."
                                               if last else None),
                "can_resume": self._active is None and blocked is None,
                "resume_blocked_reason": busy or blocked,
                "pending_refund": self._pending_refund(store, ticket_id)}

    def get_trace(self, ticket_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Trace events after the event-count cursor."""
        self._require_ticket(ticket_id)
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise AdapterError(400, "since must be a nonnegative event index.")
        return self._trace_events(ticket_id)[since:]

    def get_expected(self, ticket_id: str) -> dict[str, Any]:
        """Grader view only: the draft label compared with the latest invocation's real trace."""
        with self._lock:
            self._require_ticket(ticket_id)
            store = self._fresh()
            last = self._last_run(ticket_id)
            execution = {"last_run_id": last["run_id"], "last_mode": last.get("mode"),
                         "system": last.get("system")} if last else None
            expected = expected_outcome(self._labels[ticket_id], store.ticket_state(ticket_id),
                                        self._trace_events(ticket_id), execution, self._running(ticket_id))
        return expected

    # --- staff and policies ---

    def list_approvals(self) -> dict[str, Any]:
        """Paused tickets with what they wait for, and every staged approval with what it would unblock."""
        with self._lock:
            store = self._fresh()
            waiting = []
            for tid in sorted(store.tickets):
                if store.ticket_state(tid)["status"] != "awaiting_approval":
                    continue
                view = self._execution_view(store, tid)
                waiting.append({**self._summary(store, tid), "pending_refund": view["pending_refund"],
                                "can_resume": view["can_resume"], "resume_blocked_reason": view["resume_blocked_reason"],
                                "unresolved_requirements": (store.ticket_state(tid).get("last_result") or {})
                                .get("unresolved_requirements", [])})
            staged = []
            for item in staged_approvals().values():
                used = store.refunds(approval_id=item["approval_id"])
                staged.append({**item, "active": store.approval(item["approval_id"]) is not None,
                               "used_by_refund": used[0]["refund_id"] if used else None,
                               "unblocks": [row["ticket_id"] for row in waiting if row["pending_refund"]
                                            and row["pending_refund"]["order_id"] == item["order_id"]
                                            and row["pending_refund"]["amount_cents"] in (None, item["amount_cents"])]})
            return {"waiting": waiting, "staged": staged}

    def approve(self, approval_id: str) -> dict[str, Any]:
        """Staff activate a staged approval (the same path as `python -m agent.staff approve`). Safe to repeat."""
        with self._lock:
            if self._active is not None:
                raise AdapterError(409, "Wait for the active run before changing staff approvals.")
            store = self._fresh()
            already = store.approval(approval_id) is not None
            if already:
                return {"approval": store.approval(approval_id), "already_active": True}
            try:
                approval = staff_approve(store, approval_id)
            except ValueError as error:
                raise AdapterError(404, str(error)) from error
            return {"approval": approval, "already_active": False}

    def list_policies(self) -> list[dict[str, Any]]:
        """Policy IDs, titles and versions."""
        return [{key: p[key] for key in ("policy_id", "title", "version")} for p in self._policies.values()]

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        """One policy's metadata and sections S1-S4."""
        if policy_id not in self._policies:
            raise AdapterError(404, f"Policy {policy_id!r} was not found.")
        return deepcopy(self._policies[policy_id])

    def _evaluation(self) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Measured results from results/eval/, read on each request so a finished run shows without a restart."""
        runs, retrieval = load_measured(self._results)
        return runs, retrieval or NO_RETRIEVAL

    def list_eval_runs(self) -> list[dict[str, Any]]:
        """Evaluation runs: measured ones once agent.eval_run has written them."""
        return eval_summaries(self._evaluation()[0])

    def get_eval_run(self, run_id: str) -> dict[str, Any]:
        """One evaluation run with its per-ticket rows."""
        runs = self._evaluation()[0]
        if run_id not in runs:
            raise AdapterError(404, "Evaluation snapshot was not found.")
        return runs[run_id]

    def retrieval_metrics(self) -> dict[str, Any]:
        """Retrieval metrics written by agent.eval_retrieval --save (an empty table until then)."""
        return self._evaluation()[1]

    def reset(self) -> dict[str, int]:
        """Fresh working store: clears runs, traces, checkpoints and the outbox (the embedding cache is kept)."""
        with self._lock:
            if self._active is not None:
                raise AdapterError(409, "A run is in progress; wait for it to finish before resetting.")
            store = Store.reset(self.root)
            self._store = Store(self.root)
            return {"orders": len(store.data["orders"]), "approvals": len(store.data["approvals"])}

    # --- runs ---

    def start_run(self, ticket_id: str, system: str) -> str:
        """Start one real agent or baseline run in the background."""
        with self._lock:
            self._require_ticket(ticket_id)
            if system not in {"agent", "baseline"}:
                raise AdapterError(400, "system must be 'agent' or 'baseline'.")
            if self._active is not None:
                raise AdapterError(409, "A run is already in progress. Wait for it to finish.")
            if self._last_run(ticket_id) is not None:
                raise AdapterError(409, "This ticket already ran. Reset the workspace to run it again.")
            return self._schedule(ticket_id, system, "run")

    def start_resume(self, ticket_id: str) -> str:
        """Resume a paused ticket with the system that paused it, once its requirement can be met."""
        with self._lock:
            self._require_ticket(ticket_id)
            if self._active is not None:
                raise AdapterError(409, "A run is already in progress. Wait for it to finish.")
            store = self._fresh()
            last = self._last_run(ticket_id)
            blocked = self._resume_blocked_reason(store, ticket_id, last)
            if blocked is not None:
                raise AdapterError(400, blocked)
            return self._schedule(ticket_id, last.get("system", "agent"), "resume")

    def _schedule(self, ticket_id: str, system: str, mode: str) -> str:
        """Claim the worker (caller holds the lock) and start the run thread."""
        run_id = f"{system if mode == 'run' else 'resume'}-{uuid4().hex[:10]}"
        self._active = {"ticket_id": ticket_id, "run_id": run_id}
        Thread(target=self._worker, args=(ticket_id, system, mode, run_id), daemon=True,
               name=f"opus-real-{run_id}").start()
        return run_id

    def _components(self):
        """The model client and retriever, built on first use and reused across runs."""
        if self._llm is None:
            self._llm = self._llm_factory()
        if self._retriever is None:
            self._retriever = self._retriever_factory()
        return self._llm, self._retriever

    def _worker(self, ticket_id: str, system: str, mode: str, run_id: str) -> None:
        """Run on a private store instance; whatever happens, the ticket ends in a saved state and the worker frees."""
        store = Store(self.root)
        trace = Trace(self.root, ticket_id, run_id)
        try:
            llm, retriever = self._components()
            if system == "agent":
                run = agent_loop.resume if mode == "resume" else agent_loop.run_ticket
                run(store, ticket_id, retriever, llm, run_id=run_id)
            else:
                trace.log("run_started", mode=mode, system="baseline", model=getattr(llm, "model", CHAT_MODEL),
                          prompt_versions={"single_pass": baseline.PROMPT_VERSION})
                run = baseline.resume if mode == "resume" else baseline.run_ticket
                result = run(store, ticket_id, retriever, llm, run_id=run_id)
                trace.log("run_finished", status=result.status.value, result=result.model_dump(mode="json"),
                          budget=self._budget(Store(self.root), ticket_id, {"run_id": run_id, "system": "baseline"}))
        except Exception as error:  # e.g. missing API key or a bug: save failed_safe so the ticket isn't stuck
            reason = f"console_error: {type(error).__name__}"
            if not any(e["event"] == "run_started" and e["run_id"] == run_id for e in self._trace_events(ticket_id)):
                trace.log("run_started", mode=mode, system=system, model=CHAT_MODEL)
            trace.log("stop", reason=reason, error={"code": type(error).__name__, "message": str(error)[:300]})
            try:
                store = Store(self.root)
                category = (store.ticket_state(ticket_id).get("last_result") or {}).get("category") or Category.TECHNICAL.value
                Dispatcher(store, ticket_id, trace=trace).save_failed_safe(category, reason)
            except Exception:  # already escalated or unreadable: leave the saved state alone
                pass
            trace.log("run_finished", status=Store(self.root).ticket_state(ticket_id)["status"], result=None)
        finally:
            with self._lock:
                self._active = None
