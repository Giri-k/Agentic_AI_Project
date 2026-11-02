"""Integration checks for scripted runs, approval/resume and their in-memory boundaries."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.api.main import create_app
from ui.api.mock_adapter import MockAdapter


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def execution_client():
    """A short event delay preserves real asynchronous behavior without slowing the suite."""
    adapter = MockAdapter(step_delay=0.003)
    with TestClient(create_app(adapter=adapter)) as client:
        yield client
        wait_idle(client)


def wait_idle(client, ticket_id=None):
    """Wait a bounded time for a worker and return the resulting ticket when requested."""
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not client.get("/api/health").json()["running"]:
            return client.get(f"/api/tickets/{ticket_id}").json() if ticket_id else None
        time.sleep(0.002)
    raise AssertionError("The mock execution did not finish within the test's bounded wait")


def run_to_pause(client, system="agent"):
    """Start the required demo and verify its first invocation really pauses."""
    response = client.post("/api/tickets/T-0039/run", json={"system": system})
    assert response.status_code == 202, response.text
    assert response.json()["run_id"]
    detail = wait_idle(client, "T-0039")
    assert detail["state"]["status"] == "awaiting_approval"
    return detail


def source_snapshot():
    """Record source bytes and existing runtime files without creating or modifying them."""
    source_paths = [path for folder in (ROOT / "data" / "synthetic", ROOT / "policies")
                    for path in folder.iterdir() if path.is_file()]
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    runtime = ROOT / "runtime"
    working = {str(path.relative_to(runtime)): path.read_bytes() for path in runtime.rglob("*")
               if path.is_file()} if runtime.exists() else None
    return sources, working


def test_demo_preserves_lifetime_budget_and_executes_refund_once(execution_client):
    """The full demonstration exposes receipts and retains counters across the staff pause."""
    client = execution_client
    before = source_snapshot()
    paused = run_to_pause(client)
    assert paused["execution"]["budget"]["tool_calls"] == 4
    assert paused["execution"]["budget"]["model_calls"] == 3
    assert paused["execution"]["can_resume"] is False
    assert paused["orders"][0]["remaining_refundable_cents"] == 114900
    assert paused["orders"][0]["approvals"] == []
    assert len(paused["sent_messages"]) == 1

    initial_trace = client.get("/api/tickets/T-0039/trace").json()
    assert client.get("/api/tickets/T-0039/trace", params={"since": len(initial_trace)}).json() == []
    waiting = client.get("/api/approvals").json()
    waiting_ticket = next(row for row in waiting["waiting"] if row["ticket_id"] == "T-0039")
    assert waiting_ticket["can_resume"] is False
    assert waiting_ticket["resume_blocked_reason"]
    requirement = waiting_ticket["pending_refund"]
    assert requirement["order_id"] == "O-60048"
    assert requirement["amount_cents"] == 114900
    assert client.post("/api/tickets/T-0039/resume").status_code == 400

    first = client.post("/api/approvals/APR-8013/approve")
    assert first.status_code == 200
    assert first.json()["already_active"] is False
    assert client.post("/api/approvals/APR-8013/approve").json()["already_active"] is True
    staff_trace = client.get("/api/tickets/T-0039/trace", params={"since": len(initial_trace)}).json()
    assert len([event for event in staff_trace if event["event"] == "staff_approval"]) == 1
    assert client.get("/api/tickets/T-0039").json()["execution"]["can_resume"] is True
    approved_waiting = next(row for row in client.get("/api/approvals").json()["waiting"]
                            if row["ticket_id"] == "T-0039")
    assert approved_waiting["can_resume"] is True
    assert approved_waiting["resume_blocked_reason"] is None

    response = client.post("/api/tickets/T-0039/resume")
    assert response.status_code == 202
    finished = wait_idle(client, "T-0039")
    assert finished["state"]["status"] == "resolved"
    assert finished["execution"]["budget"]["tool_calls"] == 8
    assert finished["execution"]["budget"]["model_calls"] == 4
    order = finished["orders"][0]
    assert order["remaining_refundable_cents"] == 0
    assert order["status"] == "refunded"
    assert len(order["refunds"]) == 1
    assert order["refunds"][0]["amount_cents"] == 114900
    assert order["refunds"][0]["approval_id"] == "APR-8013"
    assert order["approvals"][0]["used_by_refund"] == order["refunds"][0]["refund_id"]
    staged = next(approval for approval in client.get("/api/approvals").json()["staged"]
                  if approval["approval_id"] == "APR-8013")
    assert staged["used_by_refund"] == order["refunds"][0]["refund_id"]
    result = finished["state"]["last_result"]
    assert order["refunds"][0]["refund_id"] in result["receipt_ids"]
    assert {"POL-RET-01", "POL-RET-02"} <= set(result["citations"])
    assert result["unresolved_requirements"] == []
    assert len(finished["sent_messages"]) == 2
    assert client.post("/api/tickets/T-0039/resume").status_code in {400, 409}
    assert client.post("/api/tickets/T-0039/run", json={"system": "baseline"}).status_code == 409
    unchanged = client.get("/api/tickets/T-0039").json()
    assert unchanged["orders"][0]["refunds"] == order["refunds"]
    assert unchanged["sent_messages"] == finished["sent_messages"]
    trace = client.get("/api/tickets/T-0039/trace").json()
    assert len({event["run_id"] for event in trace}) == 3
    assert all(event.get("ticket_id") == "T-0039" for event in trace)
    assert source_snapshot() == before


def test_one_active_run_blocks_competing_actions_atomically():
    """Parallel clicks, reset and staff mutation cannot race a working execution."""
    adapter = MockAdapter(step_delay=0.02)
    with TestClient(create_app(adapter=adapter)) as client:
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: client.post("/api/tickets/T-0039/run", json={"system": "agent"}), range(2)))
            assert sorted(response.status_code for response in responses) == [202, 409]
            assert client.get("/api/health").json()["running"] is True
            assert client.post("/api/tickets/T-0029/run", json={"system": "baseline"}).status_code == 409
            assert client.post("/api/admin/reset").status_code == 409
            assert client.post("/api/approvals/APR-8013/approve").status_code == 409
        finally:
            wait_idle(client)


def test_invalid_actions_fail_without_changing_records(execution_client):
    """Unknown IDs and unsupported transitions never silently produce a simulated success."""
    client = execution_client
    initial = client.get("/api/tickets/T-0039").json()
    assert client.post("/api/tickets/T-9999/run", json={"system": "agent"}).status_code == 404
    assert client.post("/api/tickets/T-9999/resume").status_code == 404
    assert client.post("/api/approvals/APR-9999/approve").status_code == 404
    assert client.post("/api/tickets/T-0039/resume").status_code == 400
    assert client.post("/api/tickets/T-0039/run", json={"system": "other"}).status_code == 422
    assert client.get("/api/tickets/T-0039/trace", params={"since": -1}).status_code == 422
    assert client.get("/api/tickets/T-0039").json() == initial


def test_single_pass_baseline_retrieves_once_before_its_only_model_call(execution_client):
    """The baseline uses the same records but a clearly different single-pass trajectory."""
    client = execution_client
    assert client.post("/api/tickets/T-0029/run", json={"system": "baseline"}).status_code == 202
    detail = wait_idle(client, "T-0029")
    assert detail["state"]["status"] == "resolved"
    trace = client.get("/api/tickets/T-0029/trace").json()
    models = [index for index, event in enumerate(trace) if event["event"] == "model_call"]
    lookups = [index for index, event in enumerate(trace) if event["event"] == "tool_request" and event["tool"] == "kb_lookup"]
    assert len(models) == len(lookups) == 1
    assert lookups[0] < models[0]
    assert detail["execution"]["budget"]["model_calls"] == 1
    assert detail["execution"]["budget"]["retries"] == 0
    assert client.post("/api/tickets/T-0029/run", json={"system": "agent"}).status_code == 409


def test_strict_backend_limit_stops_safely_without_resetting_resume_counters():
    """A six-call configuration reveals the unresolved demo conflict rather than hiding overflow."""
    adapter = MockAdapter(step_delay=0.001, tool_limit=6)
    with TestClient(create_app(adapter=adapter)) as client:
        run_to_pause(client)
        assert client.post("/api/approvals/APR-8013/approve").status_code == 200
        assert client.post("/api/tickets/T-0039/resume").status_code == 202
        detail = wait_idle(client, "T-0039")
        assert detail["state"]["status"] == "failed_safe"
        assert detail["execution"]["budget"]["tool_calls"] == 6
        trace = client.get("/api/tickets/T-0039/trace").json()
        assert any(event["event"] == "stop" and event.get("reason") == "tool_budget" for event in trace)
        refunds = detail["orders"][0]["refunds"]
        assert len(refunds) <= 1
        if refunds:
            assert refunds[0]["refund_id"] in detail["state"]["last_result"]["receipt_ids"]


def test_reset_restores_the_snapshot_and_removes_execution_history(execution_client):
    """Reset creates a clean comparison starting point without changing source data."""
    client = execution_client
    run_to_pause(client)
    assert client.post("/api/approvals/APR-8013/approve").status_code == 200
    assert client.post("/api/admin/reset").status_code == 200
    detail = client.get("/api/tickets/T-0039").json()
    assert detail["state"]["status"] == "new"
    assert detail["sent_messages"] == []
    assert detail["orders"][0]["approvals"] == []
    assert detail["execution"]["budget"]["tool_calls"] == 0
    assert client.get("/api/tickets/T-0039/trace").json() == []
    assert detail["execution"]["can_run"] is True
