"""Budgets survive resumes and interrupted calls without losing committed receipts."""

import pytest

from agent import agent, baseline
from agent.config import MAX_MODEL_CALLS, MAX_TOOL_CALLS
from agent.progress import load_progress
from agent.schemas import Category, Status, StopRun
from agent.staff import approve
from agent.store import Store
from test_agent import (DEMO_REFUND, DEMO_TICKET, DELIVERY_TICKET, PAUSE, ScriptedLLM,
                        classify, events, finish, kb, retriever_for, run_demo_to_pause,
                        step, supported)
from test_baseline import FakeLLM, action, fake_retriever, output


class InterruptedLLM(ScriptedLLM):
    """A process interruption does not return an ordinary model error to the caller."""

    def parse(self, messages, schema):
        if self.outputs and isinstance(self.outputs[0], BaseException):
            self.calls.append((schema, messages))
            raise self.outputs.pop(0)
        return super().parse(messages, schema)


def test_baseline_resume_keeps_the_eight_call_limit_after_reload(store):
    pause = output(status=Status.AWAITING_APPROVAL, response=PAUSE,
                   unresolved_requirements=["staff approval for 114900 cents"],
                   intended_actions=[action("send_message", ticket_id=DEMO_TICKET, body=PAUSE,
                                            citations=["POL-RET-02"])])
    baseline.run_ticket(store, DEMO_TICKET, fake_retriever, FakeLLM(pause), run_id="baseline-first")
    assert load_progress(store, DEMO_TICKET)["budget"]["tool_calls"] == 4
    approve(store, "APR-8013")
    reloaded = Store(store.root)
    reply = "Your refund of $1,149.00 has been issued."
    done = output(response=reply, intended_actions=[action("process_refund", **DEMO_REFUND),
                  action("send_message", ticket_id=DEMO_TICKET, body=reply, citations=["POL-RET-02"])])
    result = baseline.resume(reloaded, DEMO_TICKET, fake_retriever, FakeLLM(done), run_id="baseline-resumed")
    assert result.status == Status.FAILED_SAFE and result.stop_reason == "baseline_tool_budget"
    progress = load_progress(Store(store.root), DEMO_TICKET, "baseline")
    assert progress["budget"]["tool_calls"] == MAX_TOOL_CALLS
    assert progress["budget"]["model_calls"] == 2
    assert progress["in_progress"] is False
    assert len(reloaded.refunds(approval_id="APR-8013")) == 1
    executed = [e for e in events(reloaded, DEMO_TICKET) if e["event"] == "tool_result"]
    assert len(executed) == MAX_TOOL_CALLS


def test_interrupted_refund_keeps_charged_budget_and_receipt_together(store):
    run_demo_to_pause(store)
    approve(store, "APR-8013")
    interrupted = InterruptedLLM(step("process_refund", **DEMO_REFUND), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        agent.resume(store, DEMO_TICKET, retriever_for("POL-RET-01", "POL-RET-02"), interrupted)

    reloaded = Store(store.root)
    progress = load_progress(reloaded, DEMO_TICKET, "agent")
    assert progress["in_progress"] is True
    assert progress["budget"]["tool_calls"] == 6
    assert progress["budget"]["model_calls"] == 6
    assert len(reloaded.refunds(approval_id="APR-8013")) == 1
    assert agent.load_checkpoint(reloaded, DEMO_TICKET)["budget"] == progress["budget"]

    # Recovery must obey the original repetition and lifetime limits, even when it
    # needs to stop safely rather than spend enough calls to finish the reply.
    result = agent.resume(reloaded, DEMO_TICKET, retriever_for("POL-RET-01", "POL-RET-02"), ScriptedLLM())
    assert result.status == Status.FAILED_SAFE
    final = load_progress(Store(store.root), DEMO_TICKET, "agent")
    assert 6 <= final["budget"]["tool_calls"] <= MAX_TOOL_CALLS
    assert 6 <= final["budget"]["model_calls"] <= MAX_MODEL_CALLS
    assert len(Store(store.root).refunds(approval_id="APR-8013")) == 1


def test_interrupted_classification_can_resume_from_new_state(store):
    with pytest.raises(KeyboardInterrupt):
        agent.run_ticket(store, DELIVERY_TICKET, retriever_for("POL-SHIP-01"),
                         InterruptedLLM(KeyboardInterrupt()))
    reloaded = Store(store.root)
    assert reloaded.ticket_state(DELIVERY_TICKET)["status"] == "new"
    assert load_progress(reloaded, DELIVERY_TICKET)["budget"]["model_calls"] == 1
    llm = ScriptedLLM(classify(Category.DELIVERY), kb(),
                      finish("resolved", "Your order is in transit.", ["POL-SHIP-01"]), supported())
    result = agent.resume(reloaded, DELIVERY_TICKET, retriever_for("POL-SHIP-01"), llm)
    assert result.status == Status.RESOLVED
    progress = load_progress(Store(store.root), DELIVERY_TICKET, "agent")
    assert progress["budget"]["model_calls"] == 5 and progress["budget"]["tool_calls"] == 5
    assert progress["in_progress"] is False


def test_legacy_baseline_cannot_resume_with_an_unknown_lifetime_budget(store):
    with pytest.raises(ValueError, match="No saved baseline budget"):
        baseline.resume(store, DEMO_TICKET, fake_retriever, FakeLLM())


@pytest.mark.parametrize("invoke", [baseline.run_ticket, baseline.resume])
def test_unknown_baseline_ticket_stops_before_creating_execution_state(store, invoke):
    before = store.path.read_bytes()
    with pytest.raises(StopRun) as stopped:
        invoke(store, "T-9999", None, None)
    assert stopped.value.reason == "unknown_ticket"
    assert store.path.read_bytes() == before
    assert not (store.root / "traces" / "T-9999.jsonl").exists()
