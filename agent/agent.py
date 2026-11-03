"""The agent: classify, route, then a bounded plan-act-observe-decide loop over the dispatcher's tools.

Interface matches the baseline, so eval code can call either one:
    run_ticket(store, ticket_id, retriever, llm, run_id=None) -> TicketResult
    resume(store, ticket_id, retriever, llm, run_id=None) -> TicketResult

The model decides everything that needs judgement: the category, what to search for, whether to refund or
cancel, and whether to resolve, ask the customer, wait for approval or escalate. Python does the rest: it reads
the ticket at the start of every run, retries a temporary tool error once, checks the reply against the
evidence (its own checks, then a separate model call), sends the reply and saves the result with the receipts
the tools really returned. Budget and evidence live in runtime/checkpoints/<ticket>.json, so a resumed run
re-reads the records and continues on what is left of the per-ticket limits.
"""

import json
import re
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from .config import CHAT_MODEL, KB_MAX_K, MAX_MODEL_CALLS, MAX_REVISIONS, MAX_SCHEMA_REPAIRS, MAX_TOOL_CALLS
from .dispatcher import Budget, Dispatcher
from .progress import load_progress, save_progress
from .schemas import CATEGORY_TEAM, Category, Status, StopRun, Strict, TicketResult
from .store import atomic_write
from .tools import CARD_NUMBER
from .trace import Trace

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSIONS = {"classify": "classify_v1", "step": "agent_v2", "check": "check_v1"}
PROMPTS = {purpose: (PROMPT_DIR / f"{version}.txt").read_text() for purpose, version in PROMPT_VERSIONS.items()}

RESUMABLE = {"awaiting_approval", "awaiting_customer", "failed_safe"}
DEFAULT_CATEGORY = Category.TECHNICAL  # only if classification never succeeded: routes the failure to Human Review
TRUNCATED_OUTPUT = {"LengthFinishReasonError", "ContentFilterFinishReasonError"}  # bad output, so a repair may help

MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
REFUND_CLAIM = re.compile(r"\brefund\b[^!?\n]{0,60}?\b(?:has been|have been|was|is now|been)\s+(?:issued|processed|completed|sent)\b"
                          r"|\bwe(?:['’]ve| have)?\s+(?:issued|processed)\s+(?:a|your|the)\s+(?:full\s+|partial\s+)?refund\b"
                          r"|\bwe(?:['’]ve| have)\s+refunded\b", re.I)
CANCEL_CLAIM = re.compile(r"\b(?:order|it)\b[^!?\n]{0,40}?\b(?:has been|was|is now|been)\s+cancell?ed\b"
                          r"|\bwe(?:['’]ve| have)\s+cancell?ed\b", re.I)


# --- what the model returns ----------------------------------------------------

class Classification(Strict):
    """Stage 1: the category (Python routes it to a team) and what the customer wants."""
    category: Category
    summary: str


class Finish(Strict):
    """The model's decision for this run. Python checks it, sends the reply and saves it with the real receipts."""
    status: Literal["resolved", "closed", "awaiting_customer", "awaiting_approval", "escalated"]
    response: str | None
    citations: list[str]
    unresolved_requirements: list[str]
    stop_reason: str


class Step(Strict):
    """One turn of the loop: a tool call, or finish. Fields the chosen action doesn't use are null."""
    note: str
    action: Literal["kb_lookup", "process_refund", "cancel_order", "finish"]
    query: str | None
    order_id: str | None
    cents: int | None
    approval_id: str | None
    finish: Finish | None

    @model_validator(mode="after")
    def _finish_object_only_for_finish(self):
        """finish must be set exactly when the action is finish."""
        if (self.action == "finish") != (self.finish is not None):
            raise ValueError("set finish exactly when action is finish")
        return self


class ReplyCheck(Strict):
    """Stage 4: the evaluator's verdict on a draft reply."""
    supported: bool
    problems: list[str]


# --- entry points --------------------------------------------------------------

def run_ticket(store, ticket_id, retriever, llm, run_id=None):
    """First run: fresh budget and checkpoint, classify, then loop until the model finishes or a limit stops it."""
    return _AgentRun(store, ticket_id, retriever, llm, run_id or _new_id("agent"), checkpoint=None).execute()


def resume(store, ticket_id, retriever, llm, run_id=None):
    """Continue a paused ticket on its saved budget and evidence, after re-reading the current records."""
    if ticket_id not in store.tickets:
        raise StopRun("unknown_ticket")  # before reading its state
    checkpoint = load_checkpoint(store, ticket_id)
    status = store.ticket_state(ticket_id)["status"]
    progress = load_progress(store, ticket_id, "agent")
    interrupted = bool(progress and progress.get("in_progress"))
    if checkpoint is None or (status not in RESUMABLE and not (interrupted and status == "new")):
        raise ValueError(f"{ticket_id} is {status}; only a paused agent run can be resumed")
    return _AgentRun(store, ticket_id, retriever, llm, run_id or _new_id("resume"), checkpoint=checkpoint).execute()


def checkpoint_path(store, ticket_id):
    """Where a ticket's agent state is saved between runs."""
    return Path(store.root) / "checkpoints" / f"{ticket_id}.json"


def load_checkpoint(store, ticket_id):
    """The saved agent state for a ticket, or None if the agent never ran it."""
    progress = load_progress(store, ticket_id, "agent")
    if progress and progress.get("checkpoint"):
        return progress["checkpoint"]
    path = checkpoint_path(store, ticket_id)
    return json.loads(path.read_text()) if path.exists() else None


def _new_id(prefix):
    """A short unique run ID, e.g. agent-3f9c1a2b."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _model_fault(store, ticket_id):
    """The ticket's injected malformed-output failure, if any (tool and embedding faults are handled elsewhere)."""
    injected = store.failure_injection(ticket_id) or {}
    if injected.get("stage") != "model":
        return None
    return {"error": injected["error"], "remaining": -1 if injected["fail_times"] == "all" else injected["fail_times"]}


def _short_error(error):
    """A model-output error in one line, for the trace and the repair prompt."""
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'output'}: {e['msg']}" for e in error.errors()[:3])
    return f"{type(error).__name__}: {error}"[:200]


def _dollars(cents):
    """114900 -> "$1,149.00"."""
    return f"${cents / 100:,.2f}"


def _amounts_in(text):
    """Dollar amounts written in a text, as cents: "$1,149.00" -> 114900."""
    return {int(whole.replace(",", "")) * 100 + int(frac or 0) for whole, frac in MONEY.findall(text or "")}


def _cents_in(value):
    """Every *_cents number anywhere in a record."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_cents") and isinstance(item, int):
                yield item
            else:
                yield from _cents_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _cents_in(item)


# --- one invocation ------------------------------------------------------------

class _AgentRun:
    """One invocation of the agent on one ticket: a first run, or a resume of a paused one."""

    def __init__(self, store, ticket_id, retriever, llm, run_id, checkpoint):
        if ticket_id not in store.tickets:
            raise StopRun("unknown_ticket")  # spec: an unknown ticket ID stops the run before anything is written
        self.store, self.ticket_id, self.llm, self.run_id = store, ticket_id, llm, run_id
        self.mode = "resume" if checkpoint else "run"
        self.ckpt = checkpoint or {"ticket_id": ticket_id, "system": "agent", "category": None, "summary": None,
                                   "evidence": {}, "model_fault": _model_fault(store, ticket_id), "runs": []}
        budget = Budget.from_dict(checkpoint["budget"]) if checkpoint else None
        self.trace = Trace(store.root, ticket_id, run_id)
        self.in_progress = True
        self.d = Dispatcher(store, ticket_id, budget=budget, trace=self.trace, retriever=retriever,
                            on_progress=self._persist)
        self.budget = self.d.budget
        self.previous = store.ticket_state(ticket_id).get("last_result") if checkpoint else None
        self.view = None      # ticket_get result: the records as Python read them at the start of this run
        self.results = []     # this run's tool results, as the model sees them
        self.problems = []    # problems the reply check found, shown to the model for its one revision

    def execute(self):
        """Run to a saved TicketResult. Every way out (finish, pause, escalation, stop) saves a valid result."""
        self._persist()
        self._log("run_started", mode=self.mode, system="agent", model=getattr(self.llm, "model", CHAT_MODEL),
                  prompt_versions=PROMPT_VERSIONS)
        try:
            self.view = self._read_ticket()
            if self.ckpt["category"] is None:
                self._classify()
            result = self._loop()
        except StopRun as stop:
            result = self._failed(stop.reason)
        self._save_checkpoint(result)
        self._log("run_finished", status=result.status.value, result=result.model_dump(mode="json"),
                  budget=self._budget_summary())
        return result

    # --- stages ---

    def _read_ticket(self):
        """Python's own ticket_get at the start of every run (and after a version conflict)."""
        out = self._call("ticket_get", {"ticket_id": self.ticket_id}, required_read=True)
        if not out["ok"]:
            self._stop(f"ticket_get_{out['error']['code'].lower()}")
        return out["result"]

    def _classify(self):
        """Stage 1 and 2: the model picks the category; the team comes from Python's fixed table."""
        ticket = self.view["ticket"]
        content = {"subject": ticket["subject"], "channel": ticket["channel"], "messages": ticket["messages"]}
        out = self._model("classify", [{"role": "system", "content": PROMPTS["classify"]},
                                       {"role": "user", "content": json.dumps(content)}], Classification)
        self.ckpt["category"], self.ckpt["summary"] = out.category.value, out.summary
        self._persist()
        self._log("route", category=out.category.value, team=CATEGORY_TEAM[out.category].value)

    def _loop(self):
        """Stage 3: one model call per step until the model finishes with a reply that passes the checks."""
        while True:
            step = self._model("step", self._step_messages(), Step)
            if step.action == "finish":
                result = self._finish(step.finish)
            else:
                result = self._act(step)
            if result is not None:
                return result

    def _act(self, step):
        """Run the tool the model chose. A refund or cancellation needs enough budget left to tell the customer."""
        args = {"kb_lookup": {"query": step.query, "k": KB_MAX_K},
                "process_refund": {"order_id": step.order_id, "cents": step.cents, "approval_id": step.approval_id},
                "cancel_order": {"order_id": step.order_id}}[step.action]
        args = {key: value for key, value in args.items() if value is not None}  # missing ones -> INVALID_ARGS
        if step.action != "kb_lookup" and not self._room_to_finish():
            return self._save(Finish(status="escalated", response=None, citations=[], stop_reason="budget_reserve",
                                     unresolved_requirements=[f"staff to decide on {step.action} for "
                                                              f"{step.order_id}: too little run budget left to finish"]))
        out = self._call(step.action, args)
        entry = {"action": step.action, "args": args, "ok": out["ok"]}
        if not out["ok"]:
            entry["error"] = out["error"]
        elif step.action == "kb_lookup":
            entry["result"] = self._add_evidence(out["result"])
        else:
            entry["result"] = out["result"]
        self.results.append(entry)
        self._persist()
        return None

    def _finish(self, finish):
        """Stage 4: Python checks, then the evaluator. One revision is allowed; after that, escalate."""
        problems, checked_by = self._python_checks(finish), "python"
        if not problems and finish.response:
            if self._model_calls_left() < 1:
                return self._save(Finish(status="escalated", response=None, citations=[], stop_reason="budget_reserve",
                                         unresolved_requirements=["staff to review and send the drafted reply"]))
            verdict = self._model("check", self._check_messages(finish), ReplyCheck)
            checked_by = "evaluator"
            if not verdict.supported:
                problems = verdict.problems or ["the reply check found unsupported claims"]
        self._log("reply_check", supported=not problems, problems=problems, checked_by=checked_by)
        if not problems:
            return self._save(finish)
        if self.budget.revisions < MAX_REVISIONS and self._model_calls_left() >= 2:
            self.budget.revisions += 1
            self.problems = problems
            self._persist()
            self._log("revision", n=self.budget.revisions, problems=problems)
            return None
        return self._save(Finish(status="escalated", response=None, citations=[], stop_reason="unsupported_reply",
                                 unresolved_requirements=["staff review of the reply: " + "; ".join(problems)]))

    def _save(self, finish):
        """Completion gate: send the reply, then save the TicketResult with every receipt this ticket really has."""
        if finish.response and finish.citations:  # a closed note without citations is kept but not sent
            out = self._call("send_message", {"ticket_id": self.ticket_id, "body": finish.response,
                                              "citations": finish.citations})
            if not out["ok"]:
                self._stop(f"send_message_{out['error']['code'].lower()}")
        try:
            result = TicketResult(ticket_id=self.ticket_id, category=self._category(), status=Status(finish.status),
                                  response=finish.response, citations=finish.citations, receipt_ids=self._receipts(),
                                  unresolved_requirements=finish.unresolved_requirements,
                                  stop_reason=finish.stop_reason)
        except ValidationError:
            self._stop("invalid_result")
        payload = {"result": result.model_dump(mode="json"), "expected_version": self.view["version"]}
        out = self.d.call("ticket_update", json.dumps(payload))
        if not out["ok"] and out["error"]["code"] == "VERSION_CONFLICT":
            self.view = self._read_ticket()  # someone saved first: re-read, then try once more
            payload["expected_version"] = self.view["version"]
            out = self.d.call("ticket_update", json.dumps(payload))
        if not out["ok"]:
            self._stop(f"ticket_update_{out['error']['code'].lower()}")
        return result

    def _failed(self, reason):
        """Python's failed_safe save when the run must stop, keeping every receipt so far."""
        receipts = self._receipts()
        self.d.save_failed_safe(self._category().value, reason)
        return TicketResult(ticket_id=self.ticket_id, category=self._category(), status=Status.FAILED_SAFE,
                            response=None, citations=[], receipt_ids=receipts,
                            unresolved_requirements=[f"staff recovery: {reason}"], stop_reason=reason)

    # --- checks ---

    def _python_checks(self, finish):
        """Deterministic grounding checks: citations, schema rules, claimed actions and amounts."""
        problems = []
        unknown = sorted(set(finish.citations) - set(self.ckpt["evidence"]))
        if unknown:
            problems.append(f"cites {', '.join(unknown)}, which no policy search for this ticket returned")
        try:
            TicketResult(ticket_id=self.ticket_id, category=self._category(), status=Status(finish.status),
                         response=finish.response, citations=finish.citations, receipt_ids=[],
                         unresolved_requirements=finish.unresolved_requirements, stop_reason=finish.stop_reason)
        except ValidationError as e:
            problems.append(e.errors()[0]["msg"])
        if finish.status in ("resolved", "closed") and not finish.response:
            problems.append(f"a {finish.status} ticket needs a reply to the customer")
        if not finish.response:
            return problems
        if not finish.citations and finish.status != "closed":
            problems.append("the reply must cite the policies it relies on")
        if CARD_NUMBER.search(finish.response):
            problems.append("the reply contains a card number")
        receipts = self._receipts()
        if REFUND_CLAIM.search(finish.response) and not any(r.startswith("RF-") for r in receipts):
            problems.append("the reply says a refund happened, but no refund receipt exists")
        if CANCEL_CLAIM.search(finish.response) and not any(r.startswith("CAN-") for r in receipts):
            problems.append("the reply says the order was cancelled, but no cancellation receipt exists")
        unknown_amounts = sorted(_amounts_in(finish.response) - self._known_amounts())
        if unknown_amounts:
            problems.append("the reply states amounts that are not in the records or policies: "
                            + ", ".join(map(_dollars, unknown_amounts)))
        return problems

    def _known_amounts(self):
        """Amounts a reply may state: the records (and simple sums of them), the ticket text and the policies."""
        known = set(_cents_in(self.view["orders"])) | set(_cents_in(self.results))
        known |= set(_cents_in(self.view.get("prior_refunds_on_other_orders", [])))
        for order in self.view["orders"]:
            known |= {item["unit_cents"] * item["qty"] for item in order["items"]}
            captured = sum(p["amount_cents"] for p in order["payments"] if p["status"] == "captured")
            known |= {captured, captured - order["total_cents"]}
        for message in self.view["ticket"]["messages"]:
            known |= _amounts_in(message["body"])
        for passage in self.ckpt["evidence"].values():
            known |= _amounts_in(passage["text"])
        return known

    # --- model calls ---

    def _model(self, purpose, messages, schema):
        """One model call with structured output; one schema repair per ticket, then failed_safe."""
        out, error = self._ask(purpose, messages, schema)
        if error is None:
            return out
        if self.budget.schema_repairs >= MAX_SCHEMA_REPAIRS:
            self._stop("invalid_model_output")
        self.budget.schema_repairs += 1
        self._persist()
        self._log("schema_repair", n=self.budget.schema_repairs, error=error)
        repair = messages + [{"role": "user", "content": f"Your last answer could not be used ({error}). "
                                                         "Answer again with only the structured object."}]
        out, error = self._ask(purpose, repair, schema)
        if error is not None:
            self._stop("invalid_model_output")
        return out

    def _ask(self, purpose, messages, schema):
        """Spend one model call (checked against the budget first). Returns (output, None) or (None, error)."""
        if self.budget.model_calls >= MAX_MODEL_CALLS:
            self._stop("model_budget")
        self.budget.model_calls += 1
        self._persist()
        start, simulated, out, error = time.perf_counter(), False, None, None
        fault = self.ckpt["model_fault"]
        if purpose == "step" and self.ckpt["evidence"] and fault and fault["remaining"] != 0:
            fault["remaining"] -= 1 if fault["remaining"] > 0 else 0
            simulated, error = True, f"simulated {fault['error']}: the output was not valid JSON"
            self._persist()
        else:
            try:
                out = self.llm.parse(messages, schema)
                if not isinstance(out, schema):
                    raise ValueError(f"expected a {schema.__name__}")
            except (ValidationError, ValueError) as e:
                out, error = None, _short_error(e)
            except Exception as e:  # network, auth or rate limit: a repair can't fix it
                if type(e).__name__ not in TRUNCATED_OUTPUT:
                    self._log_model(purpose, None, _short_error(e), start, simulated)
                    self._stop("model_error")
                out, error = None, _short_error(e)
        self._log_model(purpose, out, error, start, simulated)
        return out, error

    def _log_model(self, purpose, out, error, start, simulated):
        """Trace one model call in the shape the UI's trace viewer reads."""
        usage = None if simulated else getattr(self.llm, "last_usage", None)
        tool_calls, text = [], error
        if isinstance(out, Step):
            tool_calls = [] if out.action == "finish" else [out.action]
            text = f"finish: {out.finish.status}. {out.note}" if out.finish else out.note
        elif isinstance(out, Classification):
            text = f"category {out.category.value}: {out.summary}"
        elif isinstance(out, ReplyCheck):
            text = "reply supported" if out.supported else "problems: " + "; ".join(out.problems)
        self._log("model_call", n=self.budget.model_calls, purpose=purpose, prompt_version=PROMPT_VERSIONS[purpose],
                  ok=error is None, tool_calls=tool_calls, text=text, simulated_fault=simulated or None,
                  tokens_in=getattr(usage, "prompt_tokens", None), tokens_out=getattr(usage, "completion_tokens", None),
                  latency_ms=round((time.perf_counter() - start) * 1000))

    def _step_messages(self):
        """What the model sees each turn: records, evidence, this run's results, open problems and budget left."""
        view, category = self.view, self.ckpt["category"]
        context = {"as_of": view["as_of"], "ticket": view["ticket"], "customer_name": view["customer_name"],
                   "ticket_status_now": view["status"], "ticket_actions_so_far": view["actions"],
                   "orders": view["orders"], "orders_not_on_this_account": view["orders_not_on_this_account"],
                   "prior_refunds_on_other_orders": view.get("prior_refunds_on_other_orders", []),
                   "category": category, "team": CATEGORY_TEAM[Category(category)].value if category else None,
                   "customer_wants": self.ckpt["summary"], "previous_result": self.previous,
                   "policy_evidence": list(self.ckpt["evidence"].values()), "this_run_results": self.results,
                   "reply_problems": self.problems,
                   "budget_left": {"model_calls": self._model_calls_left(), "tool_calls": self._tool_calls_left()}}
        return [{"role": "system", "content": PROMPTS["step"]}, {"role": "user", "content": json.dumps(context)}]

    def _check_messages(self, finish):
        """What the evaluator sees: the draft, the cited passages, the records and the real receipts."""
        content = {"status": finish.status, "draft_reply": finish.response, "citations": finish.citations,
                   "cited_policies": [self.ckpt["evidence"][p] for p in finish.citations],
                   "unresolved_requirements": finish.unresolved_requirements,
                   "records": {"as_of": self.view["as_of"], "customer_name": self.view["customer_name"],
                               "orders": self.view["orders"],
                               "orders_not_on_this_account": self.view["orders_not_on_this_account"],
                               "prior_refunds_on_other_orders": self.view.get("prior_refunds_on_other_orders", [])},
                   "receipts": self.store.ticket_state(self.ticket_id)["actions"], "this_run_results": self.results,
                   "customer_messages": [m for m in self.view["ticket"]["messages"] if m["role"] == "customer"]}
        return [{"role": "system", "content": PROMPTS["check"]}, {"role": "user", "content": json.dumps(content)}]

    # --- helpers ---

    def _call(self, tool, args, *, required_read=False):
        """One dispatcher call. A temporary error is retried once; if the retry fails too, the run stops."""
        raw = json.dumps(args)
        out = self.d.call(tool, raw, required_read=required_read)
        if not out["ok"] and out["error"]["retryable"]:
            out = self.d.call(tool, raw, required_read=required_read)
            if not out["ok"]:
                self._stop(f"{tool}_unavailable")
        return out

    def _add_evidence(self, kb):
        """Keep the passages for later steps and resumes; give the model a short summary of the search."""
        for passage in kb.get("passages", []):
            self.ckpt["evidence"][passage["policy_id"]] = passage
        return {"method": kb.get("method"), "fallback_reason": kb.get("fallback_reason"),
                "best_score": kb.get("best_score"), "weak_evidence": kb.get("weak_evidence"),
                "policy_ids": [p["policy_id"] for p in kb.get("passages", [])]}

    def _receipts(self):
        """Receipts of every action on this ticket so far (all runs), without the audit IDs of saves."""
        return [a["receipt_id"] for a in self.store.ticket_state(self.ticket_id)["actions"]
                if not a["receipt_id"].startswith("AUD-")]

    def _category(self):
        """The classified category, or the Human Review default if classification never succeeded."""
        return Category(self.ckpt["category"]) if self.ckpt["category"] else DEFAULT_CATEGORY

    def _model_calls_left(self):
        return MAX_MODEL_CALLS - self.budget.model_calls

    def _tool_calls_left(self):
        return MAX_TOOL_CALLS - self.budget.tool_calls

    def _room_to_finish(self):
        """After a refund or cancellation the run must still afford: finish + reply check, the action, send, save."""
        return self._model_calls_left() >= 2 and self._tool_calls_left() >= 3

    def _budget_summary(self):
        """Counters and limits for the trace's budget meters."""
        used = {key: getattr(self.budget, key) for key in ("model_calls", "tool_calls", "retries",
                                                             "schema_repairs", "revisions")}
        return {**used, "limits": {"model_calls": MAX_MODEL_CALLS, "tool_calls": MAX_TOOL_CALLS,
                                   "schema_repairs": MAX_SCHEMA_REPAIRS, "revisions": MAX_REVISIONS}}

    def _save_checkpoint(self, result):
        """Save budget, evidence and run history so a later resume continues from here."""
        self.ckpt["budget"] = self.budget.to_dict()
        self.ckpt["runs"].append({"run_id": self.run_id, "mode": self.mode, "status": result.status.value,
                                  "stop_reason": result.stop_reason})
        self.in_progress = False
        self._persist()
        path = checkpoint_path(self.store, self.ticket_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(self.ckpt, indent=1))

    def _persist(self):
        """Keep the current checkpoint and budget with the store before each external action."""
        self.ckpt["budget"] = self.d.budget.to_dict()
        save_progress(self.store, self.ticket_id, "agent", self.d.budget, run_id=self.run_id,
                      mode=self.mode, in_progress=self.in_progress, checkpoint=self.ckpt)

    def _stop(self, reason):
        """End the run now; execute() saves failed_safe with this reason."""
        self._log("stop", reason=reason)
        raise StopRun(reason)

    def _log(self, event, **fields):
        """Write one trace event for this run."""
        self.trace.log(event, **fields)
