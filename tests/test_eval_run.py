"""The evaluation runner: scoring rules on hand-made traces, and a full scripted run on a temp store (no network)."""

import json
import pytest

from agent import eval_run
from agent.agent import Classification, Finish, ReplyCheck, Step
from agent.schemas import Category, Status
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
    assert run["measured"] is True and "mock" not in run
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


def test_a_ticket_lost_to_an_api_timeout_is_run_again_not_scored(tmp_path):
    class TimesOutOnce(ScriptedLLM):
        def parse(self, messages, schema):
            if not getattr(self, "failed", False):
                self.failed = True
                raise type("APITimeoutError", (Exception,), {})("Request timed out.")
            return super().parse(messages, schema)
    attempts = [TimesOutOnce(*pause_script().outputs)]
    run = eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="t",
                            tools=lambda: (attempts[-1], retriever), results=tmp_path / "r", runtime=tmp_path / "rt")
    row = run["tickets"][0]
    # The first attempt ended failed_safe on the timeout; the second (same client, now healthy) is the one scored.
    assert row["attempts"] == 2 and row["actual_status"] == "awaiting_approval" and row["passed"]


def demo_run(tmp_path, **kwargs):
    return eval_run.evaluate("agent", "demo", ["T-0039"], kwargs.pop("labels", LABELS), workers=1,
                             run_id="agent-demo", tools=kwargs.pop("tools", lambda: (pause_script(), retriever)),
                             results=tmp_path / "results", runtime=tmp_path / "runtime", **kwargs)


def test_cached_run_refuses_a_different_reasoning_effort_before_constructing_clients(tmp_path):
    demo_run(tmp_path, reasoning_effort="low")
    original = (tmp_path / "results" / "agent-demo.json").read_text()
    with pytest.raises(ValueError, match="settings changed"):
        demo_run(tmp_path, reasoning_effort="high", tools=lambda: pytest.fail("must not construct clients"))
    assert (tmp_path / "results" / "agent-demo.json").read_text() == original


def test_cached_run_refuses_changed_labels_instead_of_relabelling_old_scores(tmp_path):
    demo_run(tmp_path)
    changed = {**LABELS, "T-0039": {**LABELS["T-0039"], "final_status": "escalated"}}
    with pytest.raises(ValueError, match="labels changed.*--rescore"):
        demo_run(tmp_path, labels=changed)


def test_cached_run_refuses_policy_changes(tmp_path, monkeypatch):
    demo_run(tmp_path)
    policies = eval_run.policy_texts()
    policies["POL-RET-02"] += "\nChanged approval policy.\n"
    monkeypatch.setattr(eval_run, "policy_texts", lambda: policies)
    with pytest.raises(ValueError, match="policies.*changed"):
        demo_run(tmp_path)


def test_cached_run_refuses_changed_source_records(tmp_path, monkeypatch):
    original = demo_run(tmp_path)
    changed = tmp_path / "changed-data"
    changed.mkdir()
    for name in ("store.json", "tickets.jsonl", "policy_ids.json"):
        (changed / name).write_text((DATA / name).read_text())
    (changed / "store.json").write_text((changed / "store.json").read_text() + "\n")
    monkeypatch.setattr(eval_run, "DATA", changed)
    with pytest.raises(ValueError, match="inputs.*changed"):
        demo_run(tmp_path)
    # The same labels over different input records must not advertise the same benchmark identity.
    replacement = demo_run(tmp_path, redo=True)
    assert replacement["benchmark_id"] != original["benchmark_id"]


def test_legacy_summary_cannot_be_silently_overwritten_or_used_as_a_cache(tmp_path):
    demo_run(tmp_path)
    (tmp_path / "results" / "agent-demo" / "manifest.json").unlink()
    with pytest.raises(ValueError, match="no execution manifest"):
        demo_run(tmp_path)


def test_redo_discards_judge_verdicts_and_does_not_reuse_a_previous_trace(tmp_path):
    demo_run(tmp_path)
    out = tmp_path / "results" / "agent-demo"
    (out / "judge.jsonl").write_text(json.dumps({"ticket_id": "T-0039", "error": None}) + "\n")

    def unavailable():
        raise RuntimeError("client unavailable")

    run = demo_run(tmp_path, redo=True, tools=unavailable)
    assert run["tickets"][0]["actual_status"] is None
    assert not run["tickets"][0]["trace_available"]
    assert not (out / "traces" / "T-0039.jsonl").exists()
    assert not (out / "judge.jsonl").exists()
    assert "judge_unsupported_replies" not in run["metrics"]


def test_a_cached_infrastructure_failure_invalidates_its_judge_verdict_before_retry(tmp_path):
    class Unavailable:
        model, last_usage = "fake", None

        def parse(self, messages, schema):
            raise type("APITimeoutError", (Exception,), {})("timeout")

    first = demo_run(tmp_path, tools=lambda: (Unavailable(), retriever))
    assert first["tickets"][0]["actual_status"] == "failed_safe"
    out = tmp_path / "results" / "agent-demo"
    (out / "judge.jsonl").write_text(json.dumps({"ticket_id": "T-0039", "error": None}) + "\n")
    run = demo_run(tmp_path)
    assert run["tickets"][0]["passed"]
    assert (out / "judge.jsonl").read_text() == ""
    assert "judge_unsupported_replies" not in run["metrics"]


def test_rescore_preserves_original_policy_snapshot_settings_and_creation_time(tmp_path, monkeypatch):
    original = demo_run(tmp_path, reasoning_effort="medium")
    # Historical trace bundles may predate manifests; rescoring labels still cannot change their policies.
    (tmp_path / "results" / "agent-demo" / "manifest.json").unlink()
    policies = eval_run.policy_texts()
    policies["POL-RET-02"] += "\nA later policy revision.\n"
    monkeypatch.setattr(eval_run, "policy_texts", lambda: policies)
    changed = {**LABELS, "T-0039": {**LABELS["T-0039"], "final_status": "escalated"}}
    rescored = eval_run.rescore("agent-demo", changed, results=tmp_path / "results")
    assert rescored["policy_snapshot"] == original["policy_snapshot"]
    assert rescored["settings"] == original["settings"]
    assert rescored["created_at"] == original["created_at"]
    assert rescored["benchmark_id"] != original["benchmark_id"]
    assert not rescored["tickets"][0]["status_ok"]


def test_modified_trace_is_refused_by_both_cache_reuse_and_rescoring(tmp_path):
    demo_run(tmp_path)
    path = tmp_path / "results" / "agent-demo" / "traces" / "T-0039.jsonl"
    with path.open("a") as file:
        file.write(json.dumps(ev("changed_evidence")) + "\n")
    with pytest.raises(ValueError, match="trace.*changed"):
        demo_run(tmp_path)
    with pytest.raises(ValueError, match="trace.*changed"):
        eval_run.rescore("agent-demo", LABELS, results=tmp_path / "results")


def test_summary_only_historical_run_cannot_be_rescored_without_original_evidence(tmp_path):
    demo_run(tmp_path)
    summary = tmp_path / "results" / "agent-demo.json"
    original = summary.read_text()
    (tmp_path / "results" / "agent-demo" / "rows.jsonl").unlink()
    with pytest.raises(ValueError, match="summary-only historical run"):
        eval_run.rescore("agent-demo", LABELS, results=tmp_path / "results")
    assert summary.read_text() == original


def test_rescore_does_not_invent_missing_historical_model_or_policy_metadata(tmp_path):
    demo_run(tmp_path)
    summary = tmp_path / "results" / "agent-demo.json"
    old = json.loads(summary.read_text())
    for key in ("model", "policy_snapshot", "settings"):
        del old[key]
    summary.write_text(json.dumps(old))
    (tmp_path / "results" / "agent-demo" / "manifest.json").unlink()
    rescored = eval_run.rescore("agent-demo", LABELS, results=tmp_path / "results")
    assert rescored["model"] is None and rescored["policy_snapshot"] is None
    assert rescored["settings"] == {}


# --- required actions must take effect, not just be requested ----------------------------------------

def _resume_label():
    return eval_run.resume_label(LABELS["T-0039"])  # expects process_refund with the APR-8013 arguments


def _first_read(actions=()):
    return [ev("tool_request", call_id="g", tool="ticket_get", args="{}"),
            ev("tool_result", call_id="g", tool="ticket_get", ok=True, result={"actions": list(actions)})]


def test_a_requested_refund_that_was_refused_is_a_missing_action():
    events = _first_read() + refund_events(ok=False, code="NO_MATCHING_APPROVAL")
    assert not eval_run.action_present("process_refund", events)
    row = eval_run.score(_resume_label(), {"status": "resolved", "category": "returns_refunds"},
                         {"team": "Returns", "actions": []}, [], events, 1.0)
    assert not row["actions_ok"]
    assert "Requested but did not take effect: process_refund." in row["notes"]


def test_a_refund_that_committed_before_a_timeout_still_counts_from_its_receipt():
    events = _first_read() + refund_events(ok=False, code="TIMEOUT")
    state = {"team": "Returns", "actions": [{"type": "process_refund", "receipt_id": "RF-4019"}]}
    assert "process_refund" in eval_run.committed_tools(events, state)
    assert eval_run.action_present("process_refund", events, eval_run.committed_tools(events, state))


def test_receipts_from_before_the_run_and_pythons_own_failed_safe_save_do_not_count():
    events = _first_read(actions=[{"type": "process_refund", "receipt_id": "RF-4017"}])
    state = {"actions": [{"type": "process_refund", "receipt_id": "RF-4017"},
                         {"type": "ticket_update", "receipt_id": "AUD-5001"}]}
    assert eval_run.committed_tools(events, state) == {"ticket_get"}


# --- approval-and-resume is evaluated as its own phase ----------------------------------------------

def _baseline_tools(*outputs):
    from test_baseline import FakeLLM
    llm = FakeLLM(*outputs)
    return llm, (lambda: (llm, retriever))


def _baseline_pause():
    from test_baseline import output
    # No pause message: with one, the first run uses 4 tool calls and the resume needs 5, which exceeds the
    # lifetime limit of 8 (MAX_TOOL_CALLS) and ends the resume as baseline_tool_budget.
    return output(status=Status.AWAITING_APPROVAL, response=None, citations=["POL-RET-02"],
                  unresolved_requirements=["staff approval for 114900 cents"], stop_reason="needs_staff_approval")


def _baseline_refund():
    from test_baseline import action, output
    body = "Your refund of $1,149.00 has been issued."
    return output(status=Status.RESOLVED, response=body, citations=["POL-RET-02"],
                  intended_actions=[action("process_refund", order_id="O-60048", cents=114900, approval_id="APR-8013"),
                                    action("send_message", ticket_id="T-0039", body=body, citations=["POL-RET-02"])])


def test_resume_phase_runs_after_staff_approval_and_is_scored_separately(tmp_path):
    llm, tools = _baseline_tools(_baseline_pause(), _baseline_refund())
    run = eval_run.evaluate("baseline", "demo", ["T-0039"], LABELS, workers=1, run_id="b-resume", tools=tools,
                            results=tmp_path / "results", runtime=tmp_path / "runtime")
    row = run["tickets"][0]
    assert row["actual_status"] == "awaiting_approval" and row["correct_pause"]       # first invocation
    assert row["resume_ran"] and row["resume_status"] == "resolved" and row["resume_passed"], row["resume_notes"]
    assert not row["resume_unauthorized_refund_executed"] and llm.calls == 2
    assert (tmp_path / "results" / "b-resume" / "traces" / "T-0039.resume.jsonl").exists()
    assert run["metrics"]["resume_expected"] == 1 and run["metrics"]["resume_passed"] == 1
    rescored = eval_run.rescore("b-resume", LABELS, results=tmp_path / "results")
    assert rescored["tickets"][0]["resume_passed"] and rescored["metrics"]["resume_passed"] == 1


def test_no_resume_when_the_first_run_did_not_pause_for_approval(tmp_path):
    from test_baseline import output
    escalate = output(status=Status.ESCALATED, unresolved_requirements=["staff review"], stop_reason="unsupported_request")
    llm, tools = _baseline_tools(escalate)
    run = eval_run.evaluate("baseline", "demo", ["T-0039"], LABELS, workers=1, run_id="b-noresume", tools=tools,
                            results=tmp_path / "results", runtime=tmp_path / "runtime")
    row = run["tickets"][0]
    assert row["resume_expected"] and not row["resume_ran"] and not row["resume_passed"]
    assert llm.calls == 1 and run["metrics"]["resume_passed"] == 0


def test_summary_table_names_structural_and_judge_unsupported_replies_separately():
    runs = [{"run_id": "agent-test-v2", "metrics": {"success_rate": 0.635, "unsupported_replies": 0,
                                                     "judge_unsupported_replies": 15}}]
    table = eval_run.summary_table(runs)
    assert "unsupported_replies_structural" in table and "unsupported_replies_judge" in table
    assert "success_rate_structural" in table
