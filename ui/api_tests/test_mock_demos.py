"""Checks that illustrative failure demos remain honest, isolated and replay-safe."""

import json

import pytest
from fastapi.testclient import TestClient

from ui.api.main import create_app
from ui.api.mock_adapter import MockAdapter
from ui.api_tests.test_execution_api import source_snapshot, wait_idle


@pytest.fixture
def demo_client():
    """Exercise real background scheduling with short deterministic event delays."""
    with TestClient(create_app(adapter=MockAdapter(step_delay=0.001))) as client:
        yield client
        wait_idle(client)


def start_demo(client, ticket_id, scenario="standard", system="agent"):
    """Return saved detail and its public trace after one scripted invocation."""
    response = client.post(f"/api/tickets/{ticket_id}/run", json={"system": system, "scenario": scenario})
    assert response.status_code == 202, response.text
    detail = wait_idle(client, ticket_id)
    return detail, client.get(f"/api/tickets/{ticket_id}/trace").json()


def test_timeout_retry_replays_the_committed_message_without_sending_twice(demo_client):
    """A lost response can be retried without creating a second customer message."""
    before = source_snapshot()
    detail, trace = start_demo(demo_client, "T-0052", "timeout_retry")
    assert detail["state"]["status"] == "resolved"
    assert detail["execution"]["budget"]["retries"] == 1
    assert detail["execution"]["budget"]["tool_calls"] == 5
    assert len(detail["sent_messages"]) == 1
    requests = [event for event in trace if event["event"] == "tool_request" and event["tool"] == "send_message"]
    assert len(requests) == 2
    assert json.loads(requests[0]["args"]) == json.loads(requests[1]["args"])
    responses = [event for event in trace if event["event"] == "tool_result" and event["tool"] == "send_message"]
    assert len(responses) == 2
    assert responses[0]["ok"] is False
    assert responses[0]["error"]["code"] == "TIMEOUT"
    assert responses[0]["error"]["retryable"] is True
    assert responses[1]["ok"] is True
    assert responses[1]["retry"] is True
    assert responses[1]["result"]["replayed"] is True
    assert responses[1]["result"]["receipt_id"] == detail["sent_messages"][0]["message_id"]
    assert source_snapshot() == before


def test_third_identical_call_stops_before_a_third_execution(demo_client):
    """The safety stop is visible while its refused third request costs no tool execution."""
    detail, trace = start_demo(demo_client, "T-0029", "repeated_call")
    requests = [event for event in trace if event["event"] == "tool_request" and event["tool"] == "ticket_get"]
    results = [event for event in trace if event["event"] == "tool_result" and event["tool"] == "ticket_get"]
    assert len(requests) == 3
    assert len(results) == 2
    assert detail["execution"]["budget"]["tool_calls"] == 2
    assert detail["state"]["status"] == "failed_safe"
    assert detail["sent_messages"] == []
    assert any(event["event"] == "stop" and event.get("reason") == "repeated_call" for event in trace)
    assert detail["orders"][0]["refunds"] == []


def test_forced_refund_gate_demo_does_not_change_the_refund_ledger(demo_client):
    """An explicitly forced unsafe attempt is blocked and remains distinct from normal agent behavior."""
    before = demo_client.get("/api/tickets/T-0055").json()
    detail, trace = start_demo(demo_client, "T-0055", "refund_gate")
    attempts = [event for event in trace if event["event"] == "tool_request" and event["tool"] == "process_refund"]
    failures = [event for event in trace if event["event"] == "tool_result" and event["tool"] == "process_refund"]
    assert len(attempts) == len(failures) == 1
    assert failures[0]["ok"] is False
    assert failures[0]["error"]["code"] == "NO_MATCHING_APPROVAL"
    assert detail["orders"][0]["refunds"] == before["orders"][0]["refunds"]
    assert detail["orders"][0]["remaining_refundable_cents"] == before["orders"][0]["remaining_refundable_cents"]
    assert detail["state"]["status"] == "awaiting_customer"


def test_ordinary_injection_ticket_never_attempts_the_forbidden_refund(demo_client):
    """The standard trajectory resists the injected instruction rather than intentionally exercising the gate."""
    detail, trace = start_demo(demo_client, "T-0055")
    assert detail["state"]["status"] == "awaiting_customer"
    assert not any(event["event"] == "tool_request" and event.get("tool") == "process_refund" for event in trace)
    assert detail["orders"][0]["refunds"] == []


@pytest.mark.parametrize("ticket_id,scenario,system", [
    ("T-0039", "timeout_retry", "agent"),
    ("T-0052", "refund_gate", "agent"),
    ("T-0055", "repeated_call", "agent"),
    ("T-0052", "timeout_retry", "baseline"),
    ("T-0029", "repeated_call", "baseline"),
])
def test_forced_scenario_mappings_are_rejected_without_starting(demo_client, ticket_id, scenario, system):
    """A scenario selector cannot change an unrelated ticket or redefine the single-pass baseline."""
    before = demo_client.get(f"/api/tickets/{ticket_id}").json()
    response = demo_client.post(f"/api/tickets/{ticket_id}/run", json={"system": system, "scenario": scenario})
    assert response.status_code == 400
    assert demo_client.get("/api/health").json()["running"] is False
    assert demo_client.get(f"/api/tickets/{ticket_id}").json() == before
    assert demo_client.get(f"/api/tickets/{ticket_id}/trace").json() == []
