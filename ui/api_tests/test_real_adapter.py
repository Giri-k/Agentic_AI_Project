"""RealAdapter through the HTTP routes: real store, tools, agent and baseline, with a scripted model (no network)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from agent.agent import Classification, Finish, ReplyCheck, Step
from agent.baseline import BaselineOutput, IntendedAction
from agent.schemas import Category, Status
from ui.api.main import create_app
from ui.api.real_adapter import RealAdapter

PAUSE = "Your returned ThinkPad was received. Your refund of $1,149.00 is waiting on staff approval."


class ScriptedLLM:
    """Returns the scripted outputs in order, like the agent tests' fake model."""
    model = "fake"
    last_usage = None

    def __init__(self, *outputs):
        self.outputs = list(outputs)

    def parse(self, messages, schema):
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        assert isinstance(out, schema)
        return out


def retriever(query, k):
    """Fixed policy passages in the real retriever's shape."""
    passages = [{"policy_id": p, "version": "1.1", "title": p, "score": 0.6, "best_section": "Rules",
                 "text": f"{p} v1.1: rules text."} for p in ("POL-RET-01", "POL-RET-02", "POL-SHIP-01")[:k]]
    return {"method": "embeddings", "fallback_reason": None, "best_score": 0.6, "weak_evidence": False,
            "chars": 90, "passages": passages}


def step(action, **kw):
    return Step(**{"note": f"{action} next", "action": action, "query": None, "order_id": None, "cents": None,
                   "approval_id": None, "finish": None, **kw})


def finish(status, response, citations, unresolved=(), stop_reason="done"):
    return step("finish", finish=Finish(status=status, response=response, citations=list(citations),
                                        unresolved_requirements=list(unresolved), stop_reason=stop_reason))


OK = ReplyCheck(supported=True, problems=[])
DEMO_SCRIPT = (Classification(category=Category.RETURNS_REFUNDS, summary="refund for a return"),
               step("kb_lookup", query="refund for a returned laptop"),
               finish("awaiting_approval", PAUSE, ["POL-RET-02"], ["staff approval for 114900 cents"],
                      "awaiting_refund_approval"), OK,
               step("process_refund", order_id="O-60048", cents=114900, approval_id="APR-8013"),
               finish("resolved", "Your refund of $1,149.00 has been issued.", ["POL-RET-02"]), OK)


def make_client(tmp_path, llm):
    """A console on a temp working store; the model is the given fake."""
    adapter = RealAdapter(runtime=tmp_path, llm_factory=lambda: llm, retriever_factory=lambda: retriever)
    return TestClient(create_app(adapter=adapter))


def wait_idle(client, ticket_id=None):
    """Wait a bounded time for the worker, then return the ticket detail if asked."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not client.get("/api/health").json()["running"]:
            return client.get(f"/api/tickets/{ticket_id}").json() if ticket_id else None
        time.sleep(0.01)
    raise AssertionError("the run did not finish in time")


@pytest.fixture
def demo(tmp_path):
    with make_client(tmp_path, ScriptedLLM(*DEMO_SCRIPT)) as client:
        yield client
        wait_idle(client)


def test_health_reports_real_mode_and_backend_limits(demo):
    body = demo.get("/api/health").json()
    assert body["adapter"] == "real" and body["running"] is False
    assert body["limits"]["model_calls"] == 8 and body["limits"]["tool_calls"] == 8
    assert body["budget_notice"] is None and body["capabilities"]["reset"] is True


def test_inbox_and_detail_read_the_working_store(demo):
    rows = demo.get("/api/tickets").json()
    assert len(rows) == 160 and {r["split"] for r in rows} == {"dev", "test", "demo"}
    assert "T-0039" in [r["ticket_id"] for r in demo.get("/api/tickets", params={"q": "silva"}).json()]
    detail = demo.get("/api/tickets/T-0039").json()
    assert detail["orders"][0]["order_id"] == "O-60048" and detail["orders"][0]["approvals"] == []
    assert detail["orders"][0]["remaining_refundable_cents"] == 114900
    assert detail["execution"]["can_run"] is True and detail["execution"]["can_resume"] is False
    assert "failure_injection" not in demo.get("/api/tickets/T-0052").json()["ticket"]
    assert demo.get("/api/tickets/T-9999").status_code == 404


def test_full_demo_pause_approve_resume_refund(demo):
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent"}).status_code == 202
    paused = wait_idle(demo, "T-0039")
    assert paused["state"]["status"] == "awaiting_approval"
    assert paused["execution"]["budget"]["model_calls"] == 4 and paused["execution"]["budget"]["tool_calls"] == 4
    assert paused["execution"]["pending_refund"]["amount_cents"] == 114900
    assert "Approve it" in paused["execution"]["resume_blocked_reason"]
    assert demo.post("/api/tickets/T-0039/resume").status_code == 400  # nothing to resume with yet

    approvals = demo.get("/api/approvals").json()
    assert "T-0039" in [w["ticket_id"] for w in approvals["waiting"]]
    staged = next(s for s in approvals["staged"] if s["approval_id"] == "APR-8013")
    assert staged["active"] is False and staged["unblocks"] == ["T-0039"]
    assert demo.post("/api/approvals/APR-8013/approve").json()["already_active"] is False
    assert demo.post("/api/approvals/APR-8013/approve").json()["already_active"] is True

    assert demo.post("/api/tickets/T-0039/resume").status_code == 202
    done = wait_idle(demo, "T-0039")
    assert done["state"]["status"] == "resolved"
    order = done["orders"][0]
    assert [r["refund_id"] for r in order["refunds"]] == ["RF-4019"] and order["remaining_refundable_cents"] == 0
    assert order["approvals"][0]["used_by_refund"] == "RF-4019"
    assert len(done["sent_messages"]) == 2
    assert done["execution"]["budget"]["model_calls"] == 7 and done["execution"]["budget"]["tool_calls"] == 8
    assert demo.post("/api/tickets/T-0039/resume").status_code == 400  # resolved: no second refund

    trace = demo.get("/api/tickets/T-0039/trace").json()
    runs = [e for e in trace if e["event"] == "run_started"]
    assert [r["mode"] for r in runs] == ["run", "resume"]
    assert any(e["event"] == "staff_approval" for e in trace)
    assert len(demo.get("/api/tickets/T-0039/trace", params={"since": len(trace) - 2}).json()) == 2


def test_baseline_run_gets_run_events_and_numbered_model_call(tmp_path):
    reply = "Your order is in transit with USPS."
    out = BaselineOutput(category=Category.DELIVERY, status=Status.RESOLVED, response=reply, citations=["POL-SHIP-01"],
                         intended_actions=[IntendedAction(tool="send_message", args_json=json.dumps(
                             {"ticket_id": "T-0029", "body": reply, "citations": ["POL-SHIP-01"]}))],
                         unresolved_requirements=[], stop_reason="answered")
    with make_client(tmp_path, ScriptedLLM(out)) as client:
        assert client.post("/api/tickets/T-0029/run", json={"system": "baseline"}).status_code == 202
        detail = wait_idle(client, "T-0029")
        assert detail["state"]["status"] == "resolved" and detail["execution"]["system"] == "baseline"
        assert detail["execution"]["budget"]["model_calls"] == 1
        trace = client.get("/api/tickets/T-0029/trace").json()
        assert trace[0]["event"] == "run_started" and trace[0]["system"] == "baseline"
        assert trace[-1]["event"] == "run_finished" and trace[-1]["status"] == "resolved"
        assert [e["n"] for e in trace if e["event"] == "model_call"] == [1]


def test_run_rules_reset_and_grader(demo):
    assert demo.post("/api/tickets/T-0055/run", json={"system": "agent", "scenario": "refund_gate"}).status_code == 400
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent"}).status_code == 202
    wait_idle(demo)
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent"}).status_code == 409
    expected = demo.get("/api/tickets/T-0039/expected").json()
    assert expected["mock"] is False and expected["final_status"] == "awaiting_approval"
    assert demo.post("/api/admin/reset").json()["orders"] > 0
    fresh = demo.get("/api/tickets/T-0039").json()
    assert fresh["state"]["status"] == "new" and fresh["sent_messages"] == [] and fresh["execution"]["can_run"]
    assert demo.get("/api/tickets/T-0039/trace").json() == []


def test_a_console_error_saves_failed_safe_and_frees_the_worker(tmp_path):
    def no_key():
        raise RuntimeError("PORTKEY_API_KEY is not set")
    adapter = RealAdapter(runtime=tmp_path, llm_factory=no_key, retriever_factory=lambda: retriever)
    with TestClient(create_app(adapter=adapter)) as client:
        assert client.post("/api/tickets/T-0029/run", json={"system": "agent"}).status_code == 202
        detail = wait_idle(client, "T-0029")
        assert detail["state"]["status"] == "failed_safe"
        trace = client.get("/api/tickets/T-0029/trace").json()
        assert any(e["event"] == "stop" and "RuntimeError" in e["reason"] for e in trace)
        assert trace[-1]["event"] == "run_finished"
        assert client.get("/api/health").json()["running"] is False


def test_evaluation_page_shows_measured_results_once_they_exist(tmp_path):
    results = tmp_path / "results"
    adapter = RealAdapter(runtime=tmp_path / "rt", llm_factory=lambda: None, retriever_factory=lambda: retriever,
                          results=results)
    with TestClient(create_app(adapter=adapter)) as client:
        assert all(r["mock"] for r in client.get("/api/eval/runs").json())  # nothing measured yet
        assert client.get("/api/eval/retrieval").json()["mock"] is True
        results.mkdir()
        run = {"run_id": "agent-test", "system": "agent", "split": "test", "created_at": "2026-11-02T00:00:00Z",
               "mock": False, "measured": True, "provenance": "Measured", "benchmark_id": "test-1", "policy_snapshot": "p-1",
               "metrics": {}, "by_group": [],
               "by_end_state": [], "tickets": []}
        (results / "agent-test.json").write_text(json.dumps(run))
        (results / "retrieval.json").write_text(json.dumps({"mock": False, "measured": True, "methods": []}))
        runs = client.get("/api/eval/runs").json()
        assert [r["run_id"] for r in runs] == ["agent-test"] and runs[0]["mock"] is False
        assert client.get("/api/eval/runs/agent-test").json()["measured"] is True
        assert client.get("/api/eval/runs/mock-agent-test-v1").status_code == 404
        assert client.get("/api/eval/retrieval").json()["mock"] is False
