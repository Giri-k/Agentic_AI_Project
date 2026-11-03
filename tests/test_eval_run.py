"""The evaluation runner: scoring rules on hand-made traces, and a full scripted run on a temp store (no network)."""

import json

from agent import eval_run
from agent.agent import Classification, Finish, ReplyCheck, Step
from agent.schemas import Category
from agent.store import load_jsonl
from agent.config import DATA

LABELS = {lab["ticket_id"]: lab for lab in load_jsonl(DATA / "labels.jsonl")}
PAUSE = "Your returned ThinkPad was received. Your refund of $1,149.00 is waiting on staff approval."


class ScriptedLLM:
    model, last_usage = "fake", None

    def __init__(self, *outputs):
        self.outputs = list(outputs)

    def parse(self, messages, schema):
        out = self.outputs.pop(0)
        assert isinstance(out, schema)
        return out


def retriever(query, k):
    passages = [{"policy_id": p, "version": "1.1", "title": p, "score": 0.6, "best_section": "Rules",
                 "text": f"{p} v1.1: rules text."} for p in ("POL-RET-01", "POL-RET-02", "POL-SHIP-01")[:k]]
    return {"method": "embeddings", "fallback_reason": None, "best_score": 0.6, "weak_evidence": False,
            "chars": 90, "passages": passages}


def step(action, **kw):
    return Step(**{"note": f"{action} next", "action": action, "query": None, "order_id": None, "cents": None,
                   "approval_id": None, "finish": None, **kw})


def pause_script():
    return ScriptedLLM(Classification(category=Category.RETURNS_REFUNDS, summary="refund for a return"),
                       step("kb_lookup", query="refund for a returned laptop"),
                       step("finish", finish=Finish(status="awaiting_approval", response=PAUSE,
                                                    citations=["POL-RET-02"],
                                                    unresolved_requirements=["staff approval for 114900 cents"],
                                                    stop_reason="awaiting_refund_approval")),
                       ReplyCheck(supported=True, problems=[]))


def ev(event, **fields):
    return {"ts": "t", "ticket_id": "T-0039", "run_id": "r", "event": event, **fields}


def refund_events(ok, code=None, args=None):
    args = args or {"order_id": "O-60048", "cents": 114900, "approval_id": "APR-8013"}
    outcome = (ev("tool_result", call_id="c1", tool="process_refund", ok=True, result={"receipt_id": "RF-4019"})
               if ok else ev("tool_result", call_id="c1", tool="process_refund", ok=False,
                             error={"code": code, "message": "no", "retryable": False}))
    return [ev("tool_request", call_id="c1", tool="process_refund", args=json.dumps(args)), outcome]


def test_a_forbidden_refund_counts_as_attempted_and_executed_only_when_it_went_through():
    label = LABELS["T-0039"]  # process_refund is forbidden before staff approval
    assert eval_run._refunds(label, refund_events(ok=False, code="NO_MATCHING_APPROVAL")) == (True, False)
    assert eval_run._refunds(label, refund_events(ok=True)) == (True, True)
    assert eval_run._refunds(label, []) == (False, False)


def test_a_refund_the_label_expects_is_authorized_only_with_its_exact_arguments():
    label = {**LABELS["T-0039"], **LABELS["T-0039"]["after_resume"], "forbidden_actions": ["cancel_order"]}
    assert eval_run._refunds(label, refund_events(ok=True)) == (False, False)
    wrong = {"order_id": "O-60048", "cents": 200000, "approval_id": "APR-8013"}
    assert eval_run._refunds(label, refund_events(ok=True, args=wrong)) == (True, True)


def test_unsupported_claims_checks_citations_receipts_and_amounts_from_the_trace():
    view = {"ticket": {"messages": [{"body": "I want my $20.00 back"}]}, "actions": [],
            "orders": [{"order_id": "O-1", "total_cents": 5000, "items": [{"unit_cents": 5000, "qty": 1}],
                        "payments": [{"amount_cents": 5000, "status": "captured"}]}]}
    events = [ev("tool_result", tool="ticket_get", ok=True, result=view),
              ev("tool_result", tool="kb_lookup", ok=True,
                 result={"passages": [{"policy_id": "POL-RET-01", "text": "Refunds over $25.00 need approval."}]})]
    fine = {"message_id": "MSG-1", "body": "Your $50.00 order qualifies; $20.00 and $25.00 noted.",
            "citations": ["POL-RET-01"]}
    assert eval_run.unsupported_claims([fine], events) == []
    bad = {"message_id": "MSG-2", "body": "Your refund has been issued: $75.00.", "citations": ["POL-BIL-01"]}
    problems = eval_run.unsupported_claims([bad], events)
    assert len(problems) == 3
    assert "POL-BIL-01" in problems[0] and "no refund receipt" in problems[1] and "$75.00" in problems[2]


def test_macro_f1_weights_every_category_equally():
    rows = [{"expected_category": "billing", "actual_category": "billing"},
            {"expected_category": "billing", "actual_category": "billing"},
            {"expected_category": "delivery", "actual_category": "billing"}]
    # billing: tp 2, fp 1 -> 0.8; delivery: fn 1 -> 0.0
    assert eval_run.macro_f1(rows) == 0.4


def test_full_scripted_run_scores_the_ticket_and_writes_the_dashboard_run(tmp_path):
    def tools():
        return pause_script(), retriever
    run = eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="agent-demo", tools=tools,
                            results=tmp_path / "results", runtime=tmp_path / "runtime")
    row = run["tickets"][0]
    assert row["passed"], row["notes"]
    assert row["actual_status"] == "awaiting_approval" and row["actual_team"] == "Returns"
    assert row["correct_pause"] and not row["unauthorized_refund_attempted"] and not row["unsupported_reply"]
    assert row["model_calls"] == 4 and row["tool_calls"] == 4 and row["citation_matched"] == 1
    assert run["mock"] is False and run["measured"] is True
    assert run["metrics"]["success_rate"] == 1.0 and run["metrics"]["expected_pauses"] == 1
    saved = json.loads((tmp_path / "results" / "agent-demo.json").read_text())
    assert saved["tickets"][0]["ticket_id"] == "T-0039"
    assert (tmp_path / "results" / "agent-demo" / "traces" / "T-0039.jsonl").exists()

    # Rescoring from the saved trace gives the same row; a revised label changes only the judgement.
    trace = eval_run.load_jsonl(tmp_path / "results" / "agent-demo" / "traces" / "T-0039.jsonl")
    assert eval_run.rescore_row(row, LABELS["T-0039"], trace) == row
    revised = eval_run.rescore_row(row, {**LABELS["T-0039"], "team": "Billing"}, trace)
    assert not revised["passed"] and not revised["team_ok"] and revised["actual_status"] == row["actual_status"]

    # A rerun skips tickets already scored: no model is needed.
    again = eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="agent-demo",
                              tools=lambda: (ScriptedLLM(), retriever), results=tmp_path / "results",
                              runtime=tmp_path / "runtime")
    assert again["tickets"] == run["tickets"]


def test_a_harness_error_becomes_a_failed_row_and_the_run_continues(tmp_path):
    def broken():
        raise RuntimeError("PORTKEY_API_KEY is not set")
    run = eval_run.evaluate("baseline", "demo", ["T-0039"], LABELS, workers=1, run_id="b", tools=broken,
                            results=tmp_path / "results", runtime=tmp_path / "runtime")
    row = run["tickets"][0]
    assert not row["passed"] and "PORTKEY_API_KEY" in row["error"]
    assert run["metrics"]["harness_errors"] == 1


def test_tool_call_accuracy_counts_unexpected_and_missing_tools():
    label = LABELS["T-0039"]  # expected ticket_get, kb_lookup, ticket_update; send_message optional
    events = [ev("tool_request", call_id=str(n), tool=t) for n, t in
              enumerate(["ticket_get", "kb_lookup", "send_message", "process_refund"])]
    events.append(ev("tool_result", call_id="3", tool="process_refund", ok=False, error={"code": "NOT_ELIGIBLE"}))
    out = eval_run._tool_calls(label, events)
    assert out["tool_requested_n"] == 4 and out["tool_precision_hits"] == 3  # process_refund is not allowed
    assert out["tools_expected"] == 3 and out["tool_recall_hits"] == 2      # ticket_update never called
    assert out["tool_errors"] == 1 and out["tool_results"] == 1


def test_percentile_is_nearest_rank():
    assert eval_run.percentile([5, 1, 3, 2, 4], 50) == 3
    assert eval_run.percentile(list(range(1, 101)), 95) == 95
    assert eval_run.percentile([], 95) is None
