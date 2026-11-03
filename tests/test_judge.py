"""The LLM judge on saved traces, with a fake model (no network)."""

import json

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
