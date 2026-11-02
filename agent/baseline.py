"""Single-pass baseline: ONE retrieval + ONE model call per invocation. No loop, evaluator, repair or revision.

It is the fair comparison for the full agent: same tickets, policies, store, schemas, model and the SAME
dispatcher and tools, so permission checks, receipts and safety numbers are directly comparable.
A refused action (for example NO_MATCHING_APPROVAL) is traced and the baseline does not adapt: that is the point.

Interface matches the agent's, so eval code can call either one:
    run_ticket(store, ticket_id, retriever, llm, run_id="baseline") -> TicketResult
    resume(store, ticket_id, retriever, llm, run_id="baseline-resume") -> TicketResult
Both always return a valid TicketResult for an existing ticket and never crash. The store is the source of truth
for what was actually saved.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .config import CHAT_MODEL, KB_MAX_K
from .dispatcher import Dispatcher
from .schemas import Category, Status, StopRun, Strict, TicketResult
from .trace import Trace

PROMPT_VERSION = "baseline_v1"
PROMPT = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()

# Used only when the model gave no category. Maps to the Human Review queue so a failed run lands with staff.
DEFAULT_CATEGORY = Category.TECHNICAL
QUERY_CHARS = 500
ACTION_ORDER = {"cancel_order": 0, "process_refund": 0, "send_message": 1}  # changes first, then tell the customer


class IntendedAction(Strict):
    """One action the model wants Python to run. args_json is a JSON string so strict structured output works."""
    tool: Literal["send_message", "process_refund", "cancel_order"]
    args_json: str


class BaselineOutput(Strict):
    """What the single model call returns. Python turns it into a TicketResult using the receipts that came back."""
    category: Category
    status: Status
    response: str | None
    citations: list[str]
    intended_actions: list[IntendedAction]
    unresolved_requirements: list[str]
    stop_reason: str


def run_ticket(store, ticket_id, retriever, llm, run_id="baseline"):
    """Run the single pass for one ticket and return its TicketResult (failed_safe if anything goes wrong)."""
    trace = Trace(store.root, ticket_id, run_id)
    d = Dispatcher(store, ticket_id, trace=trace, retriever=retriever)  # raises StopRun for an unknown ticket
    progress = {"category": DEFAULT_CATEGORY}
    try:
        result = _single_pass(store, d, trace, ticket_id, llm, progress)
    except StopRun as stop:  # budget spent or repeated call
        result = _failed(d, store, ticket_id, progress["category"], f"baseline_{stop.reason}")
    trace.log("final_result", system="baseline", result=result.model_dump(mode="json"))
    return result


def resume(store, ticket_id, retriever, llm, run_id="baseline-resume"):
    """A resumed ticket is a new invocation: fresh retrieval, fresh model call, fresh budget, on the CURRENT records."""
    return run_ticket(store, ticket_id, retriever, llm, run_id=run_id)


def _single_pass(store, d, trace, ticket_id, llm, progress):
    """Fetch, retrieve once, call the model once, run its actions, save. Returns the TicketResult."""
    got = d.call("ticket_get", json.dumps({"ticket_id": ticket_id}))
    if not got["ok"]:
        return _failed(d, store, ticket_id, DEFAULT_CATEGORY, f"baseline_ticket_get_{got['error']['code']}")
    view = got["result"]

    passages, note = _retrieve(d, view)
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps({"as_of": view["as_of"], "ticket": view["ticket"],
                                                        "status_now": view["status"], "orders": view["orders"],
                                                        "orders_not_on_this_account": view["orders_not_on_this_account"],
                                                        "policy_passages": passages, "policy_search": note})}]

    d.budget.model_calls += 1
    try:
        out = llm.parse(messages, BaselineOutput)
        if not isinstance(out, BaselineOutput):
            raise ValueError("model output is not a BaselineOutput")
    except Exception as e:  # no repair attempt: failed_safe is counted in the metrics, not hidden
        reason = "baseline_invalid_output" if isinstance(e, (ValidationError, ValueError)) else "baseline_model_error"
        _log_model_call(trace, llm, passages, ok=False, error=type(e).__name__)
        return _failed(d, store, ticket_id, DEFAULT_CATEGORY, reason)
    _log_model_call(trace, llm, passages, ok=True, n_actions=len(out.intended_actions))
    progress["category"] = out.category

    receipts = _run_actions(d, ticket_id, out)
    try:
        result = TicketResult(ticket_id=ticket_id, category=out.category, status=out.status, response=out.response,
                              citations=out.citations, receipt_ids=receipts,
                              unresolved_requirements=out.unresolved_requirements, stop_reason=out.stop_reason)
    except ValidationError:
        return _failed(d, store, ticket_id, out.category, "baseline_invalid_result")

    saved = d.call("ticket_update", json.dumps({"result": result.model_dump(mode="json"),
                                                "expected_version": view["version"]}))
    if not saved["ok"]:
        return _failed(d, store, ticket_id, out.category, f"baseline_ticket_update_{saved['error']['code']}")
    return result


def _retrieve(d, view):
    """Exactly one kb_lookup. On any failure: no retry, no fallback; the model just gets no passages."""
    ticket = view["ticket"]
    query = " ".join(m["body"] for m in ticket["messages"] if m["role"] == "customer")[:QUERY_CHARS] or ticket["subject"]
    try:
        kb = d.call("kb_lookup", json.dumps({"query": query, "k": KB_MAX_K}))
    except StopRun:
        raise
    except Exception as e:  # a retriever that raises something other than ToolError must not crash the run
        return [], f"policy search failed ({type(e).__name__}); no policy evidence available"
    if not kb["ok"]:
        return [], f"policy search failed ({kb['error']['code']}); no policy evidence available"
    result = kb["result"]
    items = result.get("passages", []) if isinstance(result, dict) else result
    passages = items if isinstance(items, list) else []
    weak = isinstance(result, dict) and (result.get("weak_evidence") or result.get("weak"))
    note = "evidence flagged weak" if weak else ("ok" if passages else "no passages returned")
    return passages, note


def _run_actions(d, ticket_id, out):
    """Run the model's actions through the dispatcher (changes first, then the reply). Returns receipt IDs that came back."""
    receipts = []
    for action in sorted(out.intended_actions, key=lambda a: ACTION_ORDER[a.tool]):
        raw = action.args_json
        if action.tool == "send_message":
            raw = _fill_message_args(raw, ticket_id, out.citations)
        outcome = d.call(action.tool, raw)  # refusals are traced; the baseline does not retry or adapt
        if outcome["ok"] and outcome["result"]["receipt_id"] not in receipts:
            receipts.append(outcome["result"]["receipt_id"])
    return receipts


def _fill_message_args(raw, ticket_id, citations):
    """Only fill a missing ticket_id or citations; any other model arguments are passed through untouched."""
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return raw  # the dispatcher refuses it as INVALID_ARGS
    if isinstance(args, dict):
        args.setdefault("ticket_id", ticket_id)
        args.setdefault("citations", citations)
        return json.dumps(args)
    return raw


def _failed(d, store, ticket_id, category, reason):
    """Save failed_safe through Python's own path and return the matching TicketResult, with every receipt so far."""
    state = store.ticket_state(ticket_id)
    receipts = [a["receipt_id"] for a in state["actions"] if not a["receipt_id"].startswith("AUD-")]
    d.save_failed_safe(category.value, reason)
    return TicketResult(ticket_id=ticket_id, category=category, status=Status.FAILED_SAFE, response=None,
                        citations=[], receipt_ids=receipts,
                        unresolved_requirements=[f"staff recovery: {reason}"], stop_reason=reason)


def _log_model_call(trace, llm, passages, **fields):
    """Trace the one model call: prompt version, model, which policies it saw, token usage if the client reports it."""
    ids = [p.get("source_id") or p.get("policy_id") or p.get("id") for p in passages if isinstance(p, dict)]
    usage = getattr(llm, "last_usage", None)
    trace.log("model_call", system="baseline", prompt_version=PROMPT_VERSION, model=getattr(llm, "model", CHAT_MODEL),
              passage_ids=ids, usage=usage.model_dump() if hasattr(usage, "model_dump") else None, **fields)
