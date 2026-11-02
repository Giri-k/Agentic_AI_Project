"""Contract checks for the read-only UI milestone and its fixture isolation."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "synthetic"


@pytest.fixture
def source_store():
    """Read the reference snapshot independently of the UI adapter."""
    return json.loads((DATA / "store.json").read_text())


@pytest.fixture
def source_tickets():
    """Read original tickets to locate ownership and injected-failure cases."""
    return [json.loads(line) for line in (DATA / "tickets.jsonl").read_text().splitlines() if line]


@pytest.fixture
def client(monkeypatch):
    """Create a fresh mock API per test without using any real-agent state."""
    monkeypatch.setenv("OPUS_ADAPTER", "mock")
    from ui.api.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def all_keys(value):
    """Collect nested response keys so hidden benchmark fields cannot slip through."""
    if isinstance(value, dict):
        return set(value).union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value))
    return set()


def file_hashes(paths):
    """Snapshot source bytes for the fixture-immutability check."""
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def runtime_snapshot():
    """Record existing runtime files without creating a runtime directory."""
    runtime = ROOT / "runtime"
    return {str(path.relative_to(runtime)): path.read_bytes()
            for path in runtime.rglob("*") if path.is_file()} if runtime.exists() else None


def test_health_identifies_mock_execution_and_disclosed_demo_budget(client):
    """Scripted execution remains explicitly mocked and discloses its different call allowance."""
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["adapter"] == "mock"
    assert body["as_of"] == "2026-10-20"
    assert body["running"] is False
    assert body["capabilities"]["run"] is True
    assert body["capabilities"]["resume"] is True
    assert body["limits"]["tool_calls"] == 8
    assert body["backend_limits"]["tool_calls"] == 6
    assert body["budget_notice"]


def test_inbox_preserves_fixture_state_without_answer_leak(client, source_tickets, source_store):
    """Preserve actual prior states without filling absent fields from draft answer labels."""
    response = client.get("/api/tickets")
    assert response.status_code == 200
    summaries = response.json()
    assert len(summaries) == len(source_tickets) == 160
    assert len({row["ticket_id"] for row in summaries}) == 160
    for row in summaries:
        assert row["status"] == source_store["ticket_state"][row["ticket_id"]]["status"]
        assert row["category"] is None
        assert row["team"] is None
        assert row["running"] is False
        assert row["split"] in {"dev", "test", "demo"}
    assert not all_keys(summaries) & {
        "failure_injection", "expected_actions", "forbidden_actions", "response_facts", "final_status"
    }


def test_filters_and_case_insensitive_search_select_real_tickets(client):
    """Inbox controls must operate on real fixture fields and compose correctly."""
    demo = client.get("/api/tickets", params={"split": "demo"}).json()
    assert [row["ticket_id"] for row in demo] == ["T-0039"]
    assert len(client.get("/api/tickets", params={"split": "dev"}).json()) == 44
    assert len(client.get("/api/tickets", params={"split": "test"}).json()) == 115

    selected = client.get("/api/tickets", params={
        "q": "t-0039", "priority": demo[0]["priority"],
        "channel": demo[0]["channel"], "status": "new", "split": "demo",
    }).json()
    assert [row["ticket_id"] for row in selected] == ["T-0039"]
    assert client.get("/api/tickets", params={"team": "Returns"}).json() == []
    assert client.get("/api/tickets", params={"q": "no-such-customer-xyz"}).json() == []

    customer_name = client.get("/api/tickets/T-0039").json()["customer"]["name"]
    by_customer = client.get("/api/tickets", params={"q": customer_name.swapcase()}).json()
    assert "T-0039" in {row["ticket_id"] for row in by_customer}


def test_demo_detail_keeps_staged_approval_inactive_and_money_exact(client):
    """Opening the demo must not approve or execute its refund."""
    response = client.get("/api/tickets/T-0039")
    assert response.status_code == 200
    body = response.json()
    order = body["orders"][0]
    assert order["order_id"] == "O-60048"
    assert order["total_cents"] == order["remaining_refundable_cents"] == 114900
    assert isinstance(order["remaining_refundable_cents"], int)
    assert order["approvals"] == []
    assert order["refunds"] == []
    assert body["state"]["status"] == "new"
    assert body["state"]["team"] is None
    assert body["state"]["last_result"] is None
    assert body["sent_messages"] == []
    assert body["as_of"] == "2026-10-20"


def test_ordinary_detail_never_exposes_faults_or_expected_answers(client, source_tickets):
    """Fault injection and benchmark answers belong outside the ordinary agent view."""
    ticket = next(ticket for ticket in source_tickets if "failure_injection" in ticket)
    body = client.get(f"/api/tickets/{ticket['ticket_id']}").json()
    assert not all_keys(body) & {
        "failure_injection", "expected_actions", "forbidden_actions", "response_facts",
        "scenario_type", "after_resume", "unresolved_requirements",
    }


def test_other_customers_order_details_are_never_disclosed(client, source_store, source_tickets):
    """Security scenarios may show an inaccessible ID, never another customer's records."""
    orders = {order["order_id"]: order for order in source_store["orders"]}
    foreign_cases = [ticket for ticket in source_tickets if any(
        order_id in orders and orders[order_id]["customer_id"] != ticket["customer_id"]
        for order_id in ticket["order_ids"]
    )]
    assert foreign_cases, "The benchmark should retain an ownership-isolation example"
    for ticket in foreign_cases:
        body = client.get(f"/api/tickets/{ticket['ticket_id']}").json()
        forbidden = {order_id for order_id in ticket["order_ids"]
                     if order_id in orders and orders[order_id]["customer_id"] != ticket["customer_id"]}
        assert forbidden <= set(body["orders_not_on_this_account"])
        assert not forbidden & {order["order_id"] for order in body["orders"]}
        assert all(order["customer_id"] == ticket["customer_id"] for order in body["orders"])
        for order_id in forbidden:
            assert orders[order_id]["customer_id"] not in json.dumps(body)


def test_historic_refunds_reduce_balance_and_mark_used_approvals(client, source_store, source_tickets):
    """Historical ledger entries must affect both balance and approval availability."""
    approvals = {approval["approval_id"]: approval for approval in source_store["approvals"]}
    orders = {order["order_id"]: order for order in source_store["orders"]}
    candidates = [(ticket, refund) for ticket in source_tickets for refund in source_store["refunds"]
                  if refund["order_id"] in ticket["order_ids"]
                  and orders[refund["order_id"]]["customer_id"] == ticket["customer_id"]
                  and refund["approval_id"] in approvals]
    assert candidates
    ticket, historic_refund = candidates[0]
    body = client.get(f"/api/tickets/{ticket['ticket_id']}").json()
    order = next(order for order in body["orders"] if order["order_id"] == historic_refund["order_id"])
    captured = sum(payment["amount_cents"] for payment in source_store["payments"]
                   if payment["order_id"] == order["order_id"] and payment["status"] == "captured")
    committed = sum(refund["amount_cents"] for refund in source_store["refunds"]
                    if refund["order_id"] == order["order_id"] and refund["status"] in {"issued", "settled"})
    assert order["remaining_refundable_cents"] == captured - committed
    used = next(approval for approval in order["approvals"]
                if approval["approval_id"] == historic_refund["approval_id"])
    assert used["used_by_refund"] == historic_refund["refund_id"]


def test_policy_viewer_reads_actual_versions_and_sections(client):
    """The viewer must preserve real policy versions instead of assuming all are v1.1."""
    response = client.get("/api/policies")
    assert response.status_code == 200
    policies = response.json()
    assert len(policies) == 20
    for policy_id, version in [("POL-RET-02", "1.1"), ("POL-FRD-01", "1.0")]:
        body = client.get(f"/api/policies/{policy_id}").json()
        assert body["policy_id"] == policy_id
        assert body["version"] == version
        assert body["effective_date"] == "2026-10-20"
        assert set(body["sections"]) == {"S1", "S2", "S3", "S4"}
        assert all(body["sections"].values())


@pytest.mark.parametrize("path", [
    "/api/tickets/T-9999", "/api/policies/POL-NOT-REAL", "/api/policies/%2E%2E%2Fstore",
])
def test_unknown_identifiers_and_traversal_have_no_details(client, path):
    """Unknown IDs must fail rather than fall through to filesystem or fixture contents."""
    response = client.get(path)
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_invalid_filter_is_rejected(client):
    """Filter mistakes must not silently become an apparently empty valid result."""
    assert client.get("/api/tickets", params={"status": "invented-status"}).status_code == 422


def test_reset_is_memory_only_and_public_views_are_isolated():
    """Adapter creation, view mutation and reset must preserve source and runtime files."""
    from ui.api.main import create_app
    from ui.api.mock_adapter import MockAdapter

    sources = list(DATA.glob("*")) + list((ROOT / "policies").glob("*.md"))
    sources = [path for path in sources if path.is_file()]
    before_sources, before_runtime = file_hashes(sources), runtime_snapshot()
    adapter = MockAdapter()
    detail = adapter.get_ticket("T-0039")
    detail["orders"][0]["total_cents"] = 1
    detail["ticket"]["messages"].clear()
    fresh = adapter.get_ticket("T-0039")
    assert fresh["orders"][0]["total_cents"] == 114900
    assert fresh["ticket"]["messages"]
    with TestClient(create_app(adapter=adapter)) as client:
        assert client.post("/api/admin/reset").status_code == 200
        assert client.get("/api/tickets/T-0039").json() == fresh
    assert file_hashes(sources) == before_sources
    assert runtime_snapshot() == before_runtime


def test_real_mode_never_silently_falls_back_to_mock(monkeypatch):
    """Selecting an unavailable real adapter must report the missing integration explicitly."""
    from ui.api import main

    def unavailable_real_adapter(name):
        """Simulate an absent integration even after teammates add the actual module."""
        raise ImportError("Real adapter intentionally unavailable for this test")

    monkeypatch.setenv("OPUS_ADAPTER", "real")
    monkeypatch.setattr(main, "import_module", unavailable_real_adapter)
    with TestClient(main.create_app()) as real_client:
        response = real_client.get("/api/health")
        assert response.status_code == 503
        assert "real" in response.json()["detail"].lower()


def test_cors_allows_local_ui_and_rejects_external_origins(client):
    """The local development API must not advertise wildcard browser access."""
    local = client.options("/api/tickets", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET",
    })
    assert local.status_code == 200
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"
    external = client.options("/api/tickets", headers={
        "Origin": "https://outside.example", "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in external.headers
