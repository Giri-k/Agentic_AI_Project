"""RealAdapter through the HTTP routes: real store, tools, agent and baseline, with a scripted model (no network)."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent import agent as agent_loop
from agent.agent import Classification, Finish, ReplyCheck, Step
from agent.baseline import BaselineOutput, IntendedAction
from agent.schemas import Category, Status
from agent.staff import approve as staff_approve, staged_approvals
from agent.store import Store
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
    assert body["capabilities"]["reset"] is True


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
        lookups = [i for i, e in enumerate(trace) if e["event"] == "tool_request" and e["tool"] == "kb_lookup"]
        models = [i for i, e in enumerate(trace) if e["event"] == "model_call"]
        assert len(lookups) == 1 and lookups[0] < models[0]  # one retrieval, before the only model call


def test_resume_requires_the_paused_orders_exact_approval_amount(demo):
    demo.post("/api/tickets/T-0039/run", json={"system": "agent"})
    wait_idle(demo)
    store = Store(demo.app.state.adapter.root)
    staged = staged_approvals()["APR-8013"]
    store.data["approvals"].extend([
        {**staged, "approval_id": "APR-WRONG-AMOUNT", "amount_cents": 1},
        {**staged, "approval_id": "APR-WRONG-ORDER", "order_id": "O-60001"},
    ])
    store.save()
    detail = demo.get("/api/tickets/T-0039").json()
    assert detail["execution"]["can_resume"] is False
    assert "exact order and amount" in detail["execution"]["resume_blocked_reason"]
    assert demo.post("/api/tickets/T-0039/resume").status_code == 400
    assert demo.get("/api/tickets/T-0039").json()["execution"]["budget"] == detail["execution"]["budget"]


@pytest.mark.parametrize("requirement", [
    "approval_needed for 114900 cents on order O-60048",
    "approval for 114900 cents for order O-60048 is missing",
    "staff approval for 114900 cents",
    "staff approval for order O-60048, amount 114900 cents",
    "approval required for $1,149.00 on order O-60048",
    "refund of $1149 awaiting staff approval for O-60048",
    "staff approval for USD 1149.00",
    "approval needed: 114,900 cents ($1,149.00)",
    "Staff approval needed for $1,149.00.",
    "Approval for $1149.00, order O-60048",
    "staff_approval_for_114900_cents",
    "staff_approval_for_114900_cents_on_order_O-60048",
])
def test_resume_accepts_explicit_approval_requirement_wording(demo, requirement):
    demo.post("/api/tickets/T-0039/run", json={"system": "agent"})
    wait_idle(demo)
    store = Store(demo.app.state.adapter.root)
    store.ticket_state("T-0039")["last_result"]["unresolved_requirements"] = [requirement]
    store.save()
    assert demo.get("/api/tickets/T-0039").json()["execution"]["can_resume"] is False
    demo.post("/api/approvals/APR-8013/approve")
    detail = demo.get("/api/tickets/T-0039").json()
    assert detail["execution"]["pending_refund"]["amount_cents"] == 114900
    assert detail["execution"]["can_resume"] is True
    assert demo.post("/api/tickets/T-0039/resume").status_code == 202
    done = wait_idle(demo, "T-0039")
    assert done["state"]["status"] == "resolved"
    assert len(done["orders"][0]["refunds"]) == 1
    assert done["execution"]["budget"]["tool_calls"] == 8


@pytest.mark.parametrize("requirements", [
    ["staff approval required"],
    ["approval_needed for 114900 cents", "staff approval for 1 cents"],
    ["approval_needed for 0 cents"],
    ["approval required for 1149"],
    ["approval required for $1,149.00 or $100.00"],
    ["approval required for $1149.001"],
    ["approval required for -114900 cents"],
    ["approval required for -$1149.00"],
    ["refund of $1149.00"],
])
def test_resume_does_not_guess_an_unknown_refund_amount(demo, requirements):
    demo.post("/api/tickets/T-0039/run", json={"system": "agent"})
    wait_idle(demo)
    store = Store(demo.app.state.adapter.root)
    store.ticket_state("T-0039")["last_result"]["unresolved_requirements"] = requirements
    staff_approve(store, "APR-8013")
    detail = demo.get("/api/tickets/T-0039").json()
    assert detail["execution"]["pending_refund"]["amount_cents"] is None
    assert detail["execution"]["can_resume"] is False
    assert "establish the exact refund order and amount" in detail["execution"]["resume_blocked_reason"]


@pytest.mark.parametrize("requirement", [
    "staff approval for 114900 cents on order O-60001",
    "staff_approval_for_114900_cents_on_order_O-60001",
])
def test_resume_does_not_substitute_an_owned_order_for_a_foreign_requirement(demo, requirement):
    demo.post("/api/tickets/T-0039/run", json={"system": "agent"})
    wait_idle(demo)
    store = Store(demo.app.state.adapter.root)
    store.ticket_state("T-0039")["last_result"]["unresolved_requirements"] = [requirement]
    staff_approve(store, "APR-8013")
    detail = demo.get("/api/tickets/T-0039").json()
    assert detail["execution"]["pending_refund"] is None
    assert detail["execution"]["can_resume"] is False
    assert demo.post("/api/tickets/T-0039/resume").status_code == 400


def test_interrupted_refund_has_live_counters_and_can_finish_without_another_refund(tmp_path):
    class Interrupted(BaseException):
        pass

    class InterruptAfterRefund(ScriptedLLM):
        def parse(self, messages, schema):
            if not self.outputs:
                raise Interrupted()
            return super().parse(messages, schema)

    store = Store.reset(tmp_path)
    # The initial pause need not send the optional customer message.
    pause = ScriptedLLM(Classification(category=Category.RETURNS_REFUNDS, summary="returned item"),
                        step("kb_lookup", query="returned item refund"),
                        finish("awaiting_approval", None, ["POL-RET-02"], ["staff approval for 114900 cents"]))
    agent_loop.run_ticket(store, "T-0039", retriever, pause, run_id="first")
    staff_approve(store, "APR-8013")
    with pytest.raises(Interrupted):
        agent_loop.resume(store, "T-0039", retriever, InterruptAfterRefund(
            step("process_refund", order_id="O-60048", cents=114900, approval_id="APR-8013")), run_id="interrupted")

    llm = ScriptedLLM(finish("resolved", "Your refund of $1,149.00 has been issued.", ["POL-RET-02"]), OK)
    with make_client(tmp_path, llm) as client:
        detail = client.get("/api/tickets/T-0039").json()
        assert detail["execution"]["budget"]["model_calls"] == 5
        assert detail["execution"]["budget"]["tool_calls"] == 5
        assert detail["execution"]["can_resume"] is True
        assert len(detail["orders"][0]["refunds"]) == 1
        assert client.post("/api/tickets/T-0039/resume").status_code == 202
        done = wait_idle(client, "T-0039")
        assert done["state"]["status"] == "resolved"
        assert done["execution"]["budget"]["tool_calls"] == 8
        assert done["execution"]["budget"]["model_calls"] == 7
        assert len(done["orders"][0]["refunds"]) == 1
        assert len(done["sent_messages"]) == 1


def test_run_rules_reset_and_grader(demo):
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent"}).status_code == 202
    wait_idle(demo)
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent"}).status_code == 409
    expected = demo.get("/api/tickets/T-0039/expected").json()
    assert expected["final_status"] == "awaiting_approval"
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


def test_evaluation_shows_measured_results_and_nothing_made_up_before_them(tmp_path):
    results = tmp_path / "results"
    adapter = RealAdapter(runtime=tmp_path / "rt", llm_factory=lambda: None, retriever_factory=lambda: retriever,
                          results=results)
    with TestClient(create_app(adapter=adapter)) as client:
        assert client.get("/api/eval/runs").json() == []  # nothing measured yet, and nothing invented
        empty = client.get("/api/eval/retrieval").json()
        assert empty["measured"] is False and empty["methods"] == []
        results.mkdir()
        run = {"run_id": "agent-test", "system": "agent", "split": "test", "created_at": "2026-11-02T00:00:00Z",
               "measured": True, "provenance": "Measured", "benchmark_id": "test-1", "policy_snapshot": "p-1",
               "metrics": {"success_rate": 0.5}, "settings": {"reasoning_effort": "low"}, "by_group": [],
               "by_end_state": [], "tickets": []}
        (results / "agent-test.json").write_text(json.dumps(run))
        (results / "retrieval.json").write_text(json.dumps({"measured": True, "methods": []}))
        runs = client.get("/api/eval/runs").json()
        assert [r["run_id"] for r in runs] == ["agent-test"]
        assert runs[0]["metrics"]["success_rate"] == 0.5 and runs[0]["settings"]["reasoning_effort"] == "low"
        assert client.get("/api/eval/runs/agent-test").json()["measured"] is True
        assert client.get("/api/eval/runs/unknown").status_code == 404
        assert client.get("/api/eval/retrieval").json()["measured"] is True


def test_grader_waits_for_a_finished_run_then_uses_the_after_resume_label(demo):
    """Before a run nothing is graded; after resume the approved refund is no longer forbidden."""
    before = demo.get("/api/tickets/T-0039/expected").json()
    assert before["grading"]["state"] == "not_run" and all(c["passed"] is None for c in before["grading"]["checks"])
    assert "expected_actions" not in json.dumps(demo.get("/api/tickets/T-0039").json())  # labels stay out of tickets
    demo.post("/api/tickets/T-0039/run", json={"system": "agent"})
    wait_idle(demo)
    paused = demo.get("/api/tickets/T-0039/expected").json()
    assert paused["phase"] == "initial" and paused["grading"]["state"] in {"passed", "failed"}
    assert next(c for c in paused["grading"]["checks"] if c["key"] == "response_facts")["passed"] is None
    demo.post("/api/approvals/APR-8013/approve")
    demo.post("/api/tickets/T-0039/resume")
    wait_idle(demo)
    resumed = demo.get("/api/tickets/T-0039/expected").json()
    assert resumed["phase"] == "after_resume" and resumed["final_status"] == "resolved"
    assert "process_refund" not in resumed["forbidden_actions"]
    args = next(c for c in resumed["grading"]["checks"] if c["key"] == "tool_args")
    assert args["passed"] is True and args["actual"]["process_refund"][0]["cents"] == 114900


def test_invalid_actions_fail_without_changing_records(demo):
    """Unknown IDs and unsupported requests never look like a success, and change nothing."""
    initial = demo.get("/api/tickets/T-0039").json()
    assert demo.post("/api/tickets/T-9999/run", json={"system": "agent"}).status_code == 404
    assert demo.post("/api/tickets/T-9999/resume").status_code == 404
    assert demo.post("/api/approvals/APR-9999/approve").status_code == 404
    assert demo.post("/api/tickets/T-0039/resume").status_code == 400
    assert demo.post("/api/tickets/T-0039/run", json={"system": "other"}).status_code == 422
    assert demo.post("/api/tickets/T-0039/run", json={"system": "agent", "scenario": "refund_gate"}).status_code == 422
    assert demo.get("/api/tickets/T-0039/trace", params={"since": -1}).status_code == 422
    assert demo.get("/api/tickets/T-0039").json() == initial


class HeldLLM(ScriptedLLM):
    """Waits for the test to release it, so a run can be caught while it is in progress."""

    def __init__(self, *outputs):
        super().__init__(*outputs)
        self.release = threading.Event()

    def parse(self, messages, schema):
        assert self.release.wait(10), "the test never released the model"
        return super().parse(messages, schema)


def test_one_active_run_blocks_competing_actions(tmp_path):
    """Parallel clicks, another run, reset and staff approval all wait for the running ticket."""
    llm = HeldLLM(*DEMO_SCRIPT)
    with make_client(tmp_path, llm) as client:
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: client.post("/api/tickets/T-0039/run", json={"system": "agent"}),
                                          range(2)))
            assert sorted(r.status_code for r in responses) == [202, 409]
            assert client.get("/api/health").json()["running"] is True
            assert client.post("/api/tickets/T-0029/run", json={"system": "baseline"}).status_code == 409
            assert client.post("/api/admin/reset").status_code == 409
            assert client.post("/api/approvals/APR-8013/approve").status_code == 409
        finally:
            llm.release.set()
            wait_idle(client)


@pytest.mark.parametrize('model_calls,tool_calls,ready', [(5, 4, True), (6, 4, False), (4, 5, False)])
def test_refund_resume_reserves_the_decision_reply_and_check(demo, model_calls, tool_calls, ready):
    demo.post('/api/tickets/T-0039/run', json={'system': 'agent'})
    wait_idle(demo)
    store = Store(demo.app.state.adapter.root)
    progress = store.data['execution']['T-0039']
    progress['budget']['model_calls'] = model_calls
    progress['budget']['tool_calls'] = tool_calls
    staff_approve(store, 'APR-8013')
    detail = demo.get('/api/tickets/T-0039').json()
    assert detail['execution']['can_resume'] is ready
    if not ready:
        before = store.path.read_bytes()
        assert 'Not enough remaining run budget' in detail['execution']['resume_blocked_reason']
        assert demo.post('/api/tickets/T-0039/resume').status_code == 400
        assert store.path.read_bytes() == before


def _demo_policy_search(*ids):
    def search(query, k):
        result = retriever(query, k)
        result['passages'] = [dict(result['passages'][0], policy_id=pid, title=pid) for pid in ids[:k]]
        return result
    return search


@pytest.mark.parametrize('ticket_id,category,status,policy_ids,requirements,reason', [
    ('T-0052', 'delivery', 'resolved', ['POL-SHIP-01'], [], 'done'),
    ('T-0055', 'returns_refunds', 'awaiting_customer', ['POL-SEC-01', 'POL-RET-01'],
     ['customer ships the return'], 'return_required'),
    ('T-0059', 'delivery', 'escalated', ['POL-FRD-01', 'POL-SHIP-03'],
     ['staff fraud review'], 'fraud_review'),
    ('T-0064', 'returns_refunds', 'awaiting_customer', ['POL-RET-01'],
     ['customer ships the return'], 'return_required'),
    ('T-0146', 'returns_refunds', 'awaiting_approval', ['POL-SEC-01', 'POL-RET-02'],
     ['staff_approval_for_24999_cents_on_order_O-60109'], 'awaiting_approval'),
])
def test_demo_tickets_run_through_real_api_and_guards(tmp_path, ticket_id, category, status,
                                                     policy_ids, requirements, reason):
    """Script only model decisions; keep routing, permissions, faults, receipts and UI reads real."""
    replies = {
        'T-0052': 'Your order is in transit.',
        'T-0055': 'Please send the item back through the store return process.',
        'T-0059': 'This delivery claim needs staff review.',
        'T-0064': 'Please return the item through the store return process.',
        'T-0146': 'Your return was received. A recorded staff approval is still required.',
    }
    llm = ScriptedLLM(Classification(category=Category(category), summary='support request'),
                      step('kb_lookup', query='policy for this support request'),
                      finish(status, replies[ticket_id], policy_ids, requirements, reason), OK)
    adapter = RealAdapter(runtime=tmp_path, llm_factory=lambda: llm,
                          retriever_factory=lambda: _demo_policy_search(*policy_ids))
    with TestClient(create_app(adapter=adapter)) as client:
        assert client.post(f'/api/tickets/{ticket_id}/run', json={'system': 'agent'}).status_code == 202
        done = wait_idle(client, ticket_id)
        assert done['state']['status'] == status
        trace = client.get(f'/api/tickets/{ticket_id}/trace').json()
        assert trace[-1]['event'] == 'run_finished'
        assert client.get(f'/api/tickets/{ticket_id}/expected').json()['grading']['state'] == 'passed'
        assert done['execution']['can_run'] is False
        assert client.get('/api/health').json()['running'] is False
        assert not [e for e in trace if e['event'] == 'tool_request' and e.get('tool') == 'process_refund']
        if ticket_id == 'T-0052':
            assert len(done['sent_messages']) == 1
            replies = [e for e in trace if e['event'] == 'tool_result' and e.get('tool') == 'send_message']
            assert replies[0]['error']['code'] == 'TIMEOUT'
            assert replies[-1]['retry'] is True and replies[-1]['result']['replayed'] is True
        if ticket_id == 'T-0059':
            assert done['state']['team'] == 'Human Review'
        if ticket_id == 'T-0146':
            assert done['execution']['pending_refund']['amount_cents'] == 24999
            assert done['execution']['can_resume'] is False
            assert 'No active, unused staff approval' in done['execution']['resume_blocked_reason']
        if status == 'awaiting_customer':
            assert 'Waiting for the customer' in done['execution']['resume_blocked_reason']


def test_contradictory_approval_demo_refuses_then_escalates_in_api(tmp_path):
    llm = ScriptedLLM(Classification(category=Category.RETURNS_REFUNDS, summary='refund request'),
                      step('kb_lookup', query='refund approval mismatch policy'),
                      step('process_refund', order_id='O-60059', cents=16450, approval_id='APR-8020'),
                      finish('escalated', 'The approval conflicts with the order and needs staff review.',
                             ['POL-RET-02'], ['staff reconcile approval'], 'approval_mismatch'), OK)
    adapter = RealAdapter(runtime=tmp_path, llm_factory=lambda: llm,
                          retriever_factory=lambda: _demo_policy_search('POL-RET-02'))
    with TestClient(create_app(adapter=adapter)) as client:
        client.post('/api/tickets/T-0110/run', json={'system': 'agent'})
        done = wait_idle(client, 'T-0110')
        assert done['state']['status'] == 'escalated'
        assert done['orders'][0]['refunds'] == []
        trace = client.get('/api/tickets/T-0110/trace').json()
        refused = next(e for e in trace if e['event'] == 'tool_result' and e.get('tool') == 'process_refund')
        assert refused['ok'] is False and refused['error']['code'] == 'APPROVAL_CONFLICT'
        # A correct final escalation does not hide the earlier forbidden attempt.
        grade = client.get('/api/tickets/T-0110/expected').json()['grading']
        assert grade['state'] == 'failed'
        assert next(c for c in grade['checks'] if c['key'] == 'forbidden_actions')['passed'] is False


def test_retrieval_fallback_demo_does_not_poison_the_next_ticket(tmp_path):
    import numpy as np
    from agent.retrieval import Retriever
    search = Retriever(embed_fn=lambda texts: [[1., 0.] for _ in texts], cache_path=tmp_path / 'index.json')
    llm = ScriptedLLM(
        Classification(category=Category.PRODUCT_INQUIRY, summary='shipping options'),
        step('kb_lookup', query='shipping Hawaii surcharge delivery options'),
        finish('resolved', 'Standard shipping is available to Hawaii with an additional surcharge.', ['POL-SHIP-02']), OK,
        Classification(category=Category.DELIVERY, summary='shipment tracking'),
        step('kb_lookup', query='shipment tracking'),
        finish('resolved', 'Your order is in transit.', ['POL-SHIP-01']), OK)
    adapter = RealAdapter(runtime=tmp_path, llm_factory=lambda: llm, retriever_factory=lambda: search)
    with TestClient(create_app(adapter=adapter)) as client:
        client.post('/api/tickets/T-0002/run', json={'system': 'agent'})
        assert wait_idle(client, 'T-0002')['state']['status'] == 'resolved'
        trace = client.get('/api/tickets/T-0002/trace').json()
        found = next(e['result'] for e in trace if e['event'] == 'tool_result' and e.get('tool') == 'kb_lookup')
        assert found['method'] == 'bm25' and found['fallback_reason']
        # Keep real embeddings/fault handling, but rank the tracking policy first for this deterministic test.
        search.embeddings.scores = lambda query: np.array(
            [1. if chunk.policy_id == 'POL-SHIP-01' else 0. for chunk in search.chunks])
        client.post('/api/tickets/T-0052/run', json={'system': 'agent'})
        assert wait_idle(client, 'T-0052')['state']['status'] == 'resolved'
        trace = client.get('/api/tickets/T-0052/trace').json()
        found = next(e['result'] for e in trace if e['event'] == 'tool_result' and e.get('tool') == 'kb_lookup')
        assert found['method'] == 'embeddings' and found['fallback_reason'] is None
