"""The LLM judge on saved traces, with a fake model (no network)."""

import json
import pytest

from agent import eval_run, judge
from agent.judge import FactVerdict, Judgement
from test_eval_run import LABELS, pause_script, retriever


class FakeJudge:
    model, last_usage = "fake", None

    def __init__(self, *outputs):
        self.outputs, self.seen = list(outputs), []

    def parse(self, messages, schema):
        self.seen.append(json.loads(messages[1]["content"]))
        out = self.outputs.pop(0)
        assert isinstance(out, schema)
        return out


def finished_run(tmp_path):
    """A scored T-0039 run (the agent's pause reply was sent) to judge."""
    eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="agent-demo",
                      tools=lambda: (pause_script(), retriever), results=tmp_path / "results",
                      runtime=tmp_path / "runtime")
    return tmp_path / "results"


def verdicts(*pairs, unsupported=()):
    return Judgement(facts=[FactVerdict(fact=f, verdict=v, quote="") for f, v in pairs],
                     unsupported_claims=list(unsupported))


def test_the_judge_sees_the_reply_facts_records_and_cited_policies_but_not_the_system(tmp_path):
    results = finished_run(tmp_path)
    fake = FakeJudge(verdicts(("return received", "stated"), ("refund waiting on staff approval", "missing")))
    run = judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: fake, results=results)
    case = fake.seen[0]
    assert set(case) == {"reply", "expected_facts", "records", "receipts", "policies"}
    assert "waiting on staff approval" in case["reply"][0] and list(case["policies"]) == ["POL-RET-02"]
    assert case["records"]["orders"][0]["order_id"] == "O-60048"
    dump = json.dumps(case)
    assert "baseline" not in dump and "agent-demo" not in dump and "run_id" not in dump and "system" not in case
    m, row = run["metrics"], run["tickets"][0]
    assert m["judge_fact_coverage"] == 0.5 and m["judged_replies"] == 1 and m["judge_contradicted_replies"] == 0
    assert any("refund waiting on staff approval" in n for n in row["notes"]) and row["passed"]  # judge never fails a row


def test_a_judge_answer_with_the_wrong_number_of_verdicts_is_an_error_and_is_retried(tmp_path):
    results = finished_run(tmp_path)
    judge.judge_run("agent-demo", LABELS, workers=1, results=results,
                    llm_factory=lambda: FakeJudge(verdicts(("return received", "stated"))))
    saved = eval_run.load_jsonl(results / "agent-demo" / "judge.jsonl")
    assert "1 verdicts for 2 facts" in saved[0]["error"]
    fixed = FakeJudge(verdicts(("a", "contradicted"), ("b", "stated"), unsupported=["promised a refund date"]))
    run = judge.judge_run("agent-demo", LABELS, workers=1, results=results, llm_factory=lambda: fixed)
    m = run["metrics"]
    assert m["judge_errors"] == 0 and m["judge_contradicted_replies"] == 1 and m["judge_unsupported_replies"] == 1
    facts = [f["fact"] for f in eval_run.load_jsonl(results / "agent-demo" / "judge.jsonl")[0]["facts"]]
    assert facts == LABELS["T-0039"]["response_facts"]  # the label's wording, not the judge's


def test_no_reply_means_nothing_to_judge():
    assert judge.build_case(LABELS["T-0039"], []) is None


def stated():
    return verdicts(("return received", "stated"), ("refund waiting on staff approval", "stated"))


def test_judge_reuses_a_verdict_only_for_the_same_label_trace_and_prompt(tmp_path, monkeypatch):
    results = finished_run(tmp_path)
    first = FakeJudge(stated())
    judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: first, results=results)
    assert len(first.seen) == 1
    judge.judge_run("agent-demo", LABELS, workers=1, results=results,
                    llm_factory=lambda: pytest.fail("unchanged verdict should be reused"))

    changed = {**LABELS, "T-0039": {**LABELS["T-0039"], "response_facts": ["different expected fact"]}}
    second = FakeJudge(verdicts(("different expected fact", "missing")))
    run = judge.judge_run("agent-demo", changed, workers=1, llm_factory=lambda: second, results=results)
    assert len(second.seen) == 1 and run["metrics"]["judge_fact_coverage"] == 0

    monkeypatch.setattr(judge, "PROMPT", judge.PROMPT + "\nA revised judging instruction.")
    third = FakeJudge(verdicts(("different expected fact", "stated")))
    run = judge.judge_run("agent-demo", changed, workers=1, llm_factory=lambda: third, results=results)
    assert len(third.seen) == 1 and run["metrics"]["judge_fact_coverage"] == 1


def test_replacing_an_execution_requires_a_new_judgement_even_for_the_same_ticket(tmp_path):
    results = finished_run(tmp_path)
    judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: FakeJudge(stated()), results=results)
    eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="agent-demo", redo=True,
                      tools=lambda: (pause_script(), retriever), results=results, runtime=tmp_path / "runtime")
    fresh = FakeJudge(verdicts(("return received", "missing"), ("refund waiting on staff approval", "stated")))
    run = judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: fresh, results=results)
    assert len(fresh.seen) == 1 and run["metrics"]["judge_fact_coverage"] == 0.5


def test_judge_uses_the_original_policy_evidence_after_the_live_policies_change(tmp_path, monkeypatch):
    results = finished_run(tmp_path)
    original = eval_run.policy_texts()
    changed = {**original, "POL-RET-02": "A replacement policy which was not used by this execution."}
    monkeypatch.setattr(eval_run, "policy_texts", lambda: changed)
    fake = FakeJudge(stated())
    run = judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: fake, results=results)
    assert fake.seen[0]["policies"]["POL-RET-02"] == original["POL-RET-02"]
    assert run["policy_snapshot"] == eval_run.policy_snapshot(original)


def test_legacy_verdicts_without_execution_fingerprints_are_not_reused(tmp_path):
    results = finished_run(tmp_path)
    judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: FakeJudge(stated()), results=results)
    path = results / "agent-demo" / "judge.jsonl"
    old = json.loads(path.read_text())
    del old["fingerprint"]
    path.write_text(json.dumps(old) + "\n")
    refreshed = FakeJudge(stated())
    judge.judge_run("agent-demo", LABELS, workers=1, llm_factory=lambda: refreshed, results=results)
    assert len(refreshed.seen) == 1


def test_judging_fails_before_client_creation_when_original_policy_evidence_is_unavailable(tmp_path, monkeypatch):
    results = finished_run(tmp_path)
    (results / "agent-demo" / "manifest.json").unlink()
    monkeypatch.setattr(judge, "policy_texts", lambda: {})
    with pytest.raises(ValueError, match="original policy snapshot is unavailable"):
        judge.judge_run("agent-demo", LABELS, results=results,
                        llm_factory=lambda: pytest.fail("must not construct a client"))


def test_no_reply_harness_failure_needs_no_judge_client_or_missing_trace(tmp_path):
    def unavailable():
        raise RuntimeError("No client")

    eval_run.evaluate("agent", "demo", ["T-0039"], LABELS, workers=1, run_id="failed-demo",
                      tools=unavailable, results=tmp_path / "results", runtime=tmp_path / "runtime")
    run = judge.judge_run("failed-demo", LABELS, workers=1, results=tmp_path / "results",
                          llm_factory=lambda: pytest.fail("no sent reply needs no judge client"))
    assert run["metrics"]["judged_replies"] == 0
