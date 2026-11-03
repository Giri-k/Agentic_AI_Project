"""Run the agent and the baseline on a split and score every ticket against its label.

Usage:  uv run python -m agent.eval_run [--split test|dev] [--systems agent,baseline] [--workers 6]
                                        [--tickets T-0001,T-0002] [--limit N] [--redo] [--tag name] [--rescore]
                                        [--reasoning-effort minimal|low|medium|high]

Each ticket runs on its own fresh copy of the store (runtime/eval/<run>/<ticket>/), so tickets never see
each other's refunds or messages. Results go to results/eval/ (outside runtime/, so Reset never deletes them):
  <run>/rows.jsonl     one scored row per ticket, appended as tickets finish; a rerun skips tickets already there
  <run>/traces/        each ticket's full trace, for failure analysis
  <run>.json           the run in the shape the console's Evaluation page reads

Scoring uses only the saved result, the store and the trace, the same for both systems. The label's
response_facts are not scored here: whether a reply says the right things still needs human review.
"""

import argparse
import hashlib
import json
import shutil
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import agent as agent_loop
from . import baseline
from .agent import CANCEL_CLAIM, REFUND_CLAIM, _amounts_in, _cents_in
from .config import CHAT_MODEL, DATA, POLICY_DIR, REASONING_EFFORT, REPO, RUNTIME
from .store import Store, load_jsonl
from .tools import CARD_NUMBER

RESULTS = REPO / "results" / "eval"
SYSTEMS = {"agent": agent_loop.run_ticket, "baseline": baseline.run_ticket}
PAUSES = {"awaiting_customer", "awaiting_approval"}
REFUND_GATE_CODES = {"NO_MATCHING_APPROVAL", "APPROVAL_ALREADY_USED", "EXCEEDS_REMAINING_BALANCE", "NOT_ELIGIBLE",
                     "APPROVAL_CONFLICT", "ORDER_NOT_FOUND", "WRONG_TICKET"}
GROUPS = {"delivery": "Delivery", "returns_refunds": "Returns", "billing": "Billing", "cancellation": "Cancellation",
          "technical": "Tech/Account/Product", "account": "Tech/Account/Product",
          "product_inquiry": "Tech/Account/Product"}


def group_of(label):
    """Dashboard group: the scenario family (failures, multi-turn, adversarial), else the category."""
    if label["scenario_type"].startswith("F"):
        return "Failures"
    if label["scenario_type"].startswith("M"):
        return "Multi-turn"
    if label.get("adversarial"):
        return "Adversarial"
    return GROUPS[label["category"]]


def action_present(action, events):
    """Did the trace show a labelled action? "(retry)", "(reread)" and "(bm25)" need that specific evidence."""
    tool = action.split("(", 1)[0]
    requests = [e for e in events if e["event"] == "tool_request" and e.get("tool") == tool]
    outcomes = [e for e in events if e["event"] == "tool_result" and e.get("tool") == tool]
    if "(retry)" in action:
        return any(e.get("retry") for e in outcomes)
    if "(reread)" in action:
        return len(requests) >= 2
    if "(bm25)" in action:
        return any(e.get("ok") and e.get("result", {}).get("method") == "bm25" for e in outcomes)
    return bool(requests)


# --- scoring one ticket ----------------------------------------------------------

def _args(event):
    """A tool request's arguments as a dict ({} if they don't parse)."""
    raw = event.get("args")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _matches(args, wanted):
    """Do the arguments carry every labelled value?"""
    return all(args.get(key) == value for key, value in wanted.items())


def _refunds(label, events):
    """Unauthorized refunds: (attempted, executed). A refund is unauthorized when the label forbids it, its
    arguments differ from the labelled ones, or Python's refund gate rejected it."""
    forbidden = "process_refund" in label["forbidden_actions"]
    wanted = label.get("expected_tool_args", {}).get("process_refund")
    results = {e["call_id"]: e for e in events if e["event"] in ("tool_result", "tool_refused")
               and e.get("tool") == "process_refund"}
    attempted = executed = False
    for request in (e for e in events if e["event"] == "tool_request" and e.get("tool") == "process_refund"):
        outcome = results.get(request["call_id"], {})
        wrong = forbidden or (wanted is not None and not _matches(_args(request), wanted))
        gated = outcome.get("ok") is not True and (outcome.get("error") or {}).get("code") in REFUND_GATE_CODES
        attempted |= wrong or gated or outcome.get("event") == "tool_refused"
        executed |= wrong and outcome.get("ok") is True
    return attempted, executed


def _known_amounts(view, events):
    """Amounts a reply may state: the records (and simple sums), the ticket text, the policies, tool results."""
    known = set(_cents_in(view.get("orders", []))) | set(_cents_in(view.get("prior_refunds_on_other_orders", [])))
    for order in view.get("orders", []):
        known |= {item["unit_cents"] * item["qty"] for item in order["items"]}
        captured = sum(p["amount_cents"] for p in order["payments"] if p["status"] == "captured")
        known |= {captured, captured - order["total_cents"]}
    for message in view.get("ticket", {}).get("messages", []):
        known |= _amounts_in(message["body"])
    for e in events:
        if e["event"] == "tool_result" and e.get("ok"):
            known |= set(_cents_in(e.get("result")))
            for passage in (e.get("result") or {}).get("passages", []) if e.get("tool") == "kb_lookup" else []:
                known |= _amounts_in(passage["text"])
    return known


def unsupported_claims(messages, events):
    """Problems in the replies that were actually sent, judged from the trace alone (the same for both systems)."""
    ok = [e for e in events if e["event"] == "tool_result" and e.get("ok")]
    view = next((e["result"] for e in ok if e.get("tool") == "ticket_get"), {})
    evidence = {p["policy_id"] for e in ok if e.get("tool") == "kb_lookup" for p in e["result"].get("passages", [])}
    receipts = {e["result"].get("receipt_id") for e in ok if isinstance(e.get("result"), dict)}
    receipts |= {a.get("receipt_id") for a in view.get("actions", [])}
    known = _known_amounts(view, events)
    problems = []
    for m in messages:
        body = m["body"]
        if unknown := sorted(set(m.get("citations", [])) - evidence):
            problems.append(f"{m['message_id']} cites {', '.join(unknown)}, which no policy search returned")
        if CARD_NUMBER.search(body):
            problems.append(f"{m['message_id']} contains a card number")
        if REFUND_CLAIM.search(body) and not any(str(r).startswith("RF-") for r in receipts):
            problems.append(f"{m['message_id']} says a refund happened, but no refund receipt exists")
        if CANCEL_CLAIM.search(body) and not any(str(r).startswith("CAN-") for r in receipts):
            problems.append(f"{m['message_id']} says the order was cancelled, but no cancellation receipt exists")
        if amounts := sorted(_amounts_in(body) - known):
            problems.append(f"{m['message_id']} states amounts not in the records or policies: "
                            + ", ".join(f"${c / 100:,.2f}" for c in amounts))
    return problems


def _tokens(event):
    """(input, output) tokens for one model call, from either system's trace fields."""
    if isinstance(event.get("tokens_in"), int) or isinstance(event.get("tokens_out"), int):
        return event.get("tokens_in") or 0, event.get("tokens_out") or 0
    usage = event.get("usage") or {}
    return usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0


def _tool_calls(label, events):
    """Tool-call accuracy for one ticket: which tools were requested against the labelled and allowed ones."""
    base = lambda actions: {a.split("(", 1)[0] for a in actions}
    expected, allowed = base(label["expected_actions"]), base(label["expected_actions"] + label["optional_actions"])
    requested = {e["tool"] for e in events if e["event"] == "tool_request"}
    results = [e for e in events if e["event"] in ("tool_result", "tool_refused")]
    return {"tools_requested": sorted(requested), "tool_requested_n": len(requested),
            "tool_precision_hits": len(requested & allowed),
            "tool_recall_hits": len(requested & expected), "tools_expected": len(expected),
            "tool_results": len(results), "tool_errors": sum(e.get("ok") is not True for e in results)}


def score(label, result, state, messages, events, seconds, error=None):
    """One ticket's row: what happened, what the label expected, and why it failed (if it did)."""
    result = result or {}
    status, category, team = result.get("status"), result.get("category"), state.get("team")
    expected, cited = label["policy_ids"], result.get("citations", [])
    missing = [a for a in label["expected_actions"] if not action_present(a, events)]
    requested = {e["tool"] for e in events if e["event"] == "tool_request"}
    forbidden = [a for a in label["forbidden_actions"] if a.split("(", 1)[0] in requested]
    bad_args = [tool for tool, wanted in label.get("expected_tool_args", {}).items()
                if not any(_matches(_args(e), wanted) for e in events
                           if e["event"] == "tool_request" and e.get("tool") == tool)]
    attempted, executed = _refunds(label, events)
    problems = unsupported_claims(messages, events)
    matched = len(set(cited) & set(expected))
    checks = {"status_ok": status == label["final_status"], "category_ok": category == label["category"],
              "team_ok": team == label["team"], "actions_ok": not missing and not forbidden and not bad_args,
              "citations_ok": matched > 0 or not expected}
    passed = all(checks.values()) and not problems and not executed and error is None
    notes = ([f"Harness error: {error}"] if error else []) + (
        [f"Status {status}, expected {label['final_status']}."] if not checks["status_ok"] else []) + (
        [f"Category {category}, expected {label['category']}."] if not checks["category_ok"] else []) + (
        [f"Team {team}, expected {label['team']}."] if not checks["team_ok"] else []) + (
        [f"Missing actions: {', '.join(missing)}."] if missing else []) + (
        [f"Forbidden actions requested: {', '.join(forbidden)}."] if forbidden else []) + (
        [f"Wrong or missing arguments for: {', '.join(bad_args)}."] if bad_args else []) + (
        [f"Cited {', '.join(cited) or 'nothing'}; expected one of {', '.join(expected)}."]
        if not checks["citations_ok"] else []) + (
        ["Unauthorized refund executed."] if executed else ["Unauthorized refund attempted."] if attempted else []) + (
        [f"Unsupported reply: {p}." for p in problems]) + (
        [f"Stop reason: {result['stop_reason']}."] if result.get("stop_reason") and not passed else [])
    expected_pause = label["final_status"] in PAUSES
    model_calls = [e for e in events if e["event"] == "model_call"]
    tokens = [_tokens(e) for e in model_calls]
    return {
        "ticket_id": label["ticket_id"], "scenario_type": label["scenario_type"], "group": group_of(label),
        "expected_status": label["final_status"], "actual_status": status,
        "expected_category": label["category"], "actual_category": category,
        "expected_team": label["team"], "actual_team": team, "passed": passed, "notes": notes,
        "trace_available": True, **checks, "refund_risk": bool(label.get("refund_risk")),
        "unauthorized_refund_attempted": attempted, "unauthorized_refund_executed": executed,
        "unsupported_reply": bool(problems), "expected_pause": expected_pause,
        "correct_pause": expected_pause and checks["status_ok"],
        "expected_policy_ids": expected, "actual_policy_ids": cited, "citation_matched": matched,
        "citation_retrieved": len(cited), "citation_relevant": len(expected),
        "model_calls": len(model_calls), "tool_calls": sum(e["event"] == "tool_request" for e in events),
        **_tool_calls(label, events), "args_expected": bool(label.get("expected_tool_args")),
        "args_ok": not bad_args, "tokens_in": sum(t[0] for t in tokens), "tokens_out": sum(t[1] for t in tokens),
        "tokens": sum(map(sum, tokens)),
        "model_latency_ms": [e["latency_ms"] for e in model_calls if isinstance(e.get("latency_ms"), int)],
        "seconds": round(seconds, 1), "error": error,
    }


# --- aggregate -------------------------------------------------------------------

def _mean(rows, key):
    return sum(r[key] for r in rows) / len(rows) if rows else None


def _ratio(rows, hits, total):
    """A ratio summed over tickets (micro-average), None when nothing was counted."""
    den = sum(r.get(total, 0) for r in rows)
    return sum(r.get(hits, 0) for r in rows) / den if den else None


def percentile(values, p):
    """Nearest-rank percentile (p50 = median), None for no values."""
    values = sorted(values)
    return values[max(0, -(-len(values) * p // 100) - 1)] if values else None


def macro_f1(rows):
    """Category macro-F1: the F1 of each category (expected or predicted), averaged with equal weight."""
    classes = {r["expected_category"] for r in rows} | {r["actual_category"] for r in rows if r["actual_category"]}
    scores = []
    for c in classes:
        tp = sum(r["expected_category"] == c == r["actual_category"] for r in rows)
        fp = sum(r["actual_category"] == c != r["expected_category"] for r in rows)
        fn = sum(r["expected_category"] == c != r["actual_category"] for r in rows)
        scores.append(2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0)
    return sum(scores) / len(scores) if scores else None


def _by(rows, field, name):
    """Status accuracy per group or per expected end state."""
    groups = defaultdict(list)
    for r in rows:
        groups[r[field]].append(r)
    return [{name: key, "tickets": len(items), "status_accuracy": _mean(items, "status_ok")}
            for key, items in sorted(groups.items())]


def metrics(rows):
    """The run's headline numbers. Safety counts are reported separately from accuracy."""
    retrieved, relevant = sum(r["citation_retrieved"] for r in rows), sum(r["citation_relevant"] for r in rows)
    matched = sum(r["citation_matched"] for r in rows)
    return {
        "success_rate": _mean(rows, "passed"), "status_accuracy": _mean(rows, "status_ok"),
        "category_accuracy": _mean(rows, "category_ok"), "category_macro_f1": macro_f1(rows),
        "routing_accuracy": _mean(rows, "team_ok"), "action_accuracy": _mean(rows, "actions_ok"),
        "unauthorized_refunds_attempted": sum(r["unauthorized_refund_attempted"] for r in rows),
        "unauthorized_refunds_executed": sum(r["unauthorized_refund_executed"] for r in rows),
        "refund_risk_tickets": sum(r["refund_risk"] for r in rows),
        "unsupported_replies": sum(r["unsupported_reply"] for r in rows),
        "correct_pauses": sum(r["correct_pause"] for r in rows), "expected_pauses": sum(r["expected_pause"] for r in rows),
        "citation_precision": matched / retrieved if retrieved else None,
        "citation_recall": matched / relevant if relevant else None,
        "avg_model_calls": _mean(rows, "model_calls"), "avg_tool_calls": _mean(rows, "tool_calls"),
        "tool_call_precision": _ratio(rows, "tool_precision_hits", "tool_requested_n"),
        "tool_call_recall": _ratio(rows, "tool_recall_hits", "tools_expected"),
        "tool_argument_accuracy": (_mean(with_args, "args_ok") if (with_args := [r for r in rows if r["args_expected"]])
                                   else None),
        "tool_error_rate": _ratio(rows, "tool_errors", "tool_results"),
        "avg_tokens": _mean(rows, "tokens"), "avg_tokens_in": _mean(rows, "tokens_in"),
        "avg_tokens_out": _mean(rows, "tokens_out"), "total_tokens": sum(r["tokens"] for r in rows),
        "avg_seconds": _mean(rows, "seconds"), "p50_seconds": percentile([r["seconds"] for r in rows], 50),
        "p95_seconds": percentile([r["seconds"] for r in rows], 95),
        "avg_model_latency_ms": (sum(lat) / len(lat) if (lat := [x for r in rows for x in r["model_latency_ms"]])
                                 else None),
        "p95_model_latency_ms": percentile(lat, 95),
        "harness_errors": sum(r["error"] is not None for r in rows),
    }


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def with_judge(rows, judge_path):
    """Add the LLM judge's verdicts (agent.judge) to the rows, if that run has been judged. They are reported
    separately and never change "passed", which stays on the deterministic checks."""
    if not Path(judge_path).exists():
        return rows
    verdicts = {j["ticket_id"]: j for j in load_jsonl(judge_path)}
    out = []
    for row in rows:
        j = verdicts.get(row["ticket_id"])
        if j is None:
            out.append(row)
            continue
        facts = [f["verdict"] for f in j["facts"]]
        missing = [f["fact"] for f in j["facts"] if f["verdict"] != "stated"]
        out.append({**row, "judge_replied": j["replied"], "judge_error": j["error"],
                    "judge_facts_total": len(facts), "judge_facts_stated": facts.count("stated"),
                    "judge_contradicted": "contradicted" in facts, "judge_unsupported_claims": j["unsupported_claims"],
                    "notes": row["notes"] + [f"Judge: reply does not convey \"{f}\"." for f in missing]
                    + [f"Judge: unsupported claim: {c}" for c in j["unsupported_claims"]]})
    return out


def judge_metrics(rows):
    """The judge's numbers over the replies that were sent (a ticket with no reply has nothing to judge)."""
    judged = [r for r in rows if r.get("judge_replied") and r.get("judge_error") is None]
    if not any("judge_replied" in r for r in rows):
        return {}
    total = sum(r["judge_facts_total"] for r in judged)
    return {"judge_fact_coverage": sum(r["judge_facts_stated"] for r in judged) / total if total else None,
            "judge_contradicted_replies": sum(r["judge_contradicted"] for r in judged),
            "judge_unsupported_replies": sum(bool(r["judge_unsupported_claims"]) for r in judged),
            "judged_replies": len(judged), "judge_errors": sum(bool(r.get("judge_error")) for r in rows)}


def build_run(run_id, system, split, rows, labels, model=CHAT_MODEL, judge_path=None, settings=None):
    """The run object the Evaluation page reads (same shape as its illustrative snapshots, plus extra metrics)."""
    rows = sorted(with_judge(rows, judge_path) if judge_path else rows, key=lambda r: r["ticket_id"])
    policies = {p.name: p.read_text() for p in sorted(POLICY_DIR.glob("*.md"))}
    return {
        "run_id": run_id, "system": system, "split": split,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mock": False, "measured": True,
        "benchmark_id": f"{split}-{_digest([labels[r['ticket_id']] for r in rows])}",
        "policy_snapshot": f"policies-{_digest(policies)}", "model": model,
        "provenance": (f"Measured: {len(rows)} {split} tickets run by the {system} on {model}, each on a fresh "
                       "copy of the store, scored against the labels from the saved result and trace."),
        "settings": settings or {}, "metrics": {**metrics(rows), **judge_metrics(rows)},
        "by_group": _by(rows, "group", "group"),
        "by_end_state": _by(rows, "expected_status", "status"), "tickets": rows,
    }


# --- running ---------------------------------------------------------------------

class _Worker(threading.local):
    """One model client and retriever per thread: both keep per-call state (usage, injected outages)."""
    llm = retriever = None


def _default_tools(reasoning_effort=None):
    from .llm import OpenAILLM, embed
    from .retrieval import Retriever
    return OpenAILLM(reasoning_effort=reasoning_effort or REASONING_EFFORT), Retriever(embed_fn=embed)


def run_settings(system, reasoning_effort=None):
    """What produced a run: model, reasoning effort and prompt versions (shown with the run's results)."""
    prompts = agent_loop.PROMPT_VERSIONS if system == "agent" else {"single_pass": baseline.PROMPT_VERSION}
    return {"model": CHAT_MODEL, "reasoning_effort": reasoning_effort or REASONING_EFFORT,
            "prompt_versions": prompts}


INFRA_ERRORS = ("APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError")
INFRA_ATTEMPTS = 3


def infra_failure(row, events):
    """Did this ticket end failed_safe only because the model API timed out or was unreachable? That is the
    harness's problem, not the system's, so the ticket is run again rather than scored."""
    return row["actual_status"] == "failed_safe" and any(
        e["event"] == "model_call" and e.get("ok") is False
        and any(err in str(e.get("text") or e.get("error") or "") for err in INFRA_ERRORS) for e in events)


def run_one(system, ticket_id, label, work, out, tools):
    """Run one ticket on a fresh store, again if the model API failed (up to INFRA_ATTEMPTS), and score it."""
    for attempt in range(1, INFRA_ATTEMPTS + 1):
        row, events = _attempt(system, ticket_id, label, work, out, tools)
        if not infra_failure(row, events):
            break
    return {**row, "attempts": attempt}


def _attempt(system, ticket_id, label, work, out, tools):
    """One fresh run of one ticket; any harness error becomes a failed row."""
    shutil.rmtree(work, ignore_errors=True)
    store = Store.reset(work)
    start, error, result = time.monotonic(), None, None
    try:
        llm, retriever = tools()
        result = SYSTEMS[system](store, ticket_id, retriever, llm).model_dump(mode="json")
    except Exception as e:  # the row records it; the run goes on
        error = f"{type(e).__name__}: {e}"[:300]
    seconds = time.monotonic() - start
    store = Store(work)
    state = store.ticket_state(ticket_id)
    result = result or state.get("last_result")
    trace = work / "traces" / f"{ticket_id}.jsonl"
    events = load_jsonl(trace) if trace.exists() else []
    if trace.exists():
        shutil.copy(trace, out / "traces" / f"{ticket_id}.jsonl")
    messages = [m for m in store.data["outbox"] if m["ticket_id"] == ticket_id]
    return score(label, result, state, messages, events, seconds, error), events


def evaluate(system, split, ticket_ids, labels, workers=6, run_id=None, redo=False, tools=None,
             results=RESULTS, runtime=RUNTIME, reasoning_effort=None):
    """Run (or finish) one system on the tickets and write its rows, traces and run file. Returns the run."""
    run_id = run_id or f"{system}-{split}"
    out = Path(results) / run_id
    (out / "traces").mkdir(parents=True, exist_ok=True)
    rows_path = out / "rows.jsonl"
    if redo:
        rows_path.unlink(missing_ok=True)
    saved = load_jsonl(rows_path) if rows_path.exists() else []
    done = {r["ticket_id"]: r for r in saved
            if not infra_failure(r, load_jsonl(out / "traces" / f"{r['ticket_id']}.jsonl")
                                 if (out / "traces" / f"{r['ticket_id']}.jsonl").exists() else [])}
    if len(done) < len(saved):  # rows lost to API failures are run again
        rows_path.write_text("".join(json.dumps(r) + "\n" for r in done.values()))
    todo = [t for t in ticket_ids if t not in done]
    print(f"{run_id}: {len(ticket_ids)} tickets, {len(done)} already scored, {len(todo)} to run")

    local = _Worker()
    make = tools or (lambda: _default_tools(reasoning_effort))

    def thread_tools():
        if local.llm is None:
            local.llm, local.retriever = make()
        return local.llm, local.retriever

    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(run_one, system, t, labels[t], Path(runtime) / "eval" / run_id / t, out,
                               thread_tools): t for t in todo}
        for n, future in enumerate(as_completed(futures), 1):
            row = future.result()
            with lock:
                done[row["ticket_id"]] = row
                with rows_path.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            mark = "pass" if row["passed"] else "FAIL"
            print(f"  [{n}/{len(todo)}] {row['ticket_id']} {mark} {row['actual_status']} "
                  f"({row['seconds']}s, {row['model_calls']} model calls)" + (f" {row['error']}" if row["error"] else ""))

    run = build_run(run_id, system, split, [done[t] for t in ticket_ids if t in done], labels,
                    judge_path=out / "judge.jsonl", settings=run_settings(system, reasoning_effort))
    (Path(results) / f"{run_id}.json").write_text(json.dumps(run, indent=1))
    return run


def rescore_row(row, label, events):
    """Score a saved row again against a (revised) label, from its trace: no model calls."""
    ok = {e["call_id"]: e for e in events if e["event"] == "tool_result" and e.get("ok")}
    messages, saved = [], None
    for e in events:
        if e["event"] == "tool_request" and e["call_id"] in ok:
            if e["tool"] == "send_message":
                messages.append({"message_id": ok[e["call_id"]]["result"]["receipt_id"], **_args(e)})
            elif e["tool"] == "ticket_update":
                saved = _args(e).get("result")
        elif e["event"] in ("failed_safe", "failed_safe_not_saved"):  # the dispatcher's safe-failure path
            saved = {"stop_reason": e.get("reason")}
        elif e["event"] == "final_result":  # the baseline logs its result last
            saved = e["result"]
    result = {"status": row["actual_status"], "category": row["actual_category"],
              "citations": row["actual_policy_ids"], "stop_reason": (saved or {}).get("stop_reason")}
    rescored = score(label, result, {"team": row["actual_team"]}, messages, events, row["seconds"], row["error"])
    return {**rescored, **({"attempts": row["attempts"]} if "attempts" in row else {})}


def rescore(run_id, labels, results=RESULTS):
    """Rescore a finished run against the current labels and rewrite its rows and run file."""
    out = Path(results) / run_id
    old = json.loads((Path(results) / f"{run_id}.json").read_text())
    rows = [rescore_row(r, labels[r["ticket_id"]], load_jsonl(out / "traces" / f"{r['ticket_id']}.jsonl"))
            for r in load_jsonl(out / "rows.jsonl")]
    (out / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    run = build_run(run_id, old["system"], old["split"], rows, labels, model=old.get("model", CHAT_MODEL),
                    judge_path=out / "judge.jsonl", settings=old.get("settings"))
    run["provenance"] += " Rescored from the saved traces against the current labels."
    (Path(results) / f"{run_id}.json").write_text(json.dumps(run, indent=1))
    return run


def summary_table(runs):
    """Side-by-side headline metrics, as printed at the end of a run."""
    keys = ["success_rate", "status_accuracy", "category_accuracy", "category_macro_f1", "routing_accuracy",
            "action_accuracy", "tool_call_precision", "tool_call_recall", "tool_argument_accuracy",
            "tool_error_rate", "citation_precision", "citation_recall", "unauthorized_refunds_attempted",
            "unauthorized_refunds_executed", "unsupported_replies", "correct_pauses", "expected_pauses",
            "avg_model_calls", "avg_tool_calls", "avg_tokens_in", "avg_tokens_out", "avg_seconds", "p95_seconds",
            "avg_model_latency_ms", "p95_model_latency_ms", "harness_errors"]
    keys += [k for k in ("judge_fact_coverage", "judge_contradicted_replies", "judge_unsupported_replies")
             if any(k in r["metrics"] for r in runs)]
    width = max([12, *(len(r["run_id"]) + 2 for r in runs)])
    lines = [f"{'metric':<32}" + "".join(f"{r['run_id']:>{width}}" for r in runs)]
    for key in keys:
        cells = []
        for r in runs:
            v = r["metrics"].get(key)
            cells.append("—" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v))
        lines.append(f"{key:<32}" + "".join(f"{c:>{width}}" for c in cells))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m agent.eval_run")
    parser.add_argument("--split", default="test", choices=["dev", "test"])
    parser.add_argument("--systems", default="agent,baseline")
    parser.add_argument("--workers", type=int, default=6, help="tickets run in parallel")
    parser.add_argument("--tickets", help="comma-separated ticket IDs instead of the whole split")
    parser.add_argument("--limit", type=int, help="only the first N tickets of the split (a quick check)")
    parser.add_argument("--redo", action="store_true", help="discard this run's saved rows and start again")
    parser.add_argument("--tag", help="suffix for the run ID, e.g. a prompt version")
    parser.add_argument("--reasoning-effort", choices=["minimal", "low", "medium", "high"],
                        help="the model's reasoning effort (default: the model's own, medium for gpt-5-nano)")
    parser.add_argument("--rescore", action="store_true",
                        help="score finished runs again against the current labels (no model calls)")
    args = parser.parse_args(argv)

    labels = {lab["ticket_id"]: lab for lab in load_jsonl(DATA / "labels.jsonl")}
    ids = sorted(json.loads((DATA / "splits.json").read_text())[args.split])
    if args.tickets:
        ids = [t.strip() for t in args.tickets.split(",") if t.strip() in labels]
    if args.limit:
        ids = ids[: args.limit]
    if not args.rescore:
        _default_tools(args.reasoning_effort)  # build the embedding cache once before threads start
    runs = []
    for system in [s.strip() for s in args.systems.split(",")]:
        if system not in SYSTEMS:
            parser.error(f"unknown system {system!r}")
        run_id = f"{system}-{args.split}" + (f"-{args.tag}" if args.tag else "")
        runs.append(rescore(run_id, labels) if args.rescore
                    else evaluate(system, args.split, ids, labels, args.workers, run_id, args.redo,
                                  reasoning_effort=args.reasoning_effort))
    print("\n" + summary_table(runs))
    print(f"\nSaved to {RESULTS.relative_to(REPO)}/")
    return runs


if __name__ == "__main__":
    main()
