"""Checks for explicit grader access and honest, internally consistent mock dashboard artifacts."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.api.main import create_app
from ui.api.mock_adapter import MockAdapter
from ui.api_tests.test_execution_api import wait_idle


@pytest.fixture
def client():
    """Use isolated, quick scripted runs while retaining actual fixture membership."""
    with TestClient(create_app(adapter=MockAdapter(step_delay=0.001))) as source:
        yield source
        wait_idle(source)


def test_grader_only_checks_completed_observations_and_marks_response_semantics_unverified(client):
    """A view of the label alone must never count as an evaluated model result."""
    before = client.get('/api/tickets/T-0039/expected').json()
    assert before['grading']['state'] == 'not_run'
    assert all(check['passed'] is None for check in before['grading']['checks'])
    ordinary = client.get('/api/tickets/T-0039').json()
    assert 'grading' not in ordinary
    assert 'expected_actions' not in json.dumps(ordinary)
    assert client.post('/api/tickets/T-0039/run', json={'system': 'agent'}).status_code == 202
    wait_idle(client)
    grade = client.get('/api/tickets/T-0039/expected').json()
    assert grade['phase'] == 'initial'
    assert grade['grading']['state'] == 'passed'
    assert grade['mock'] is True
    semantic = next(check for check in grade['grading']['checks'] if check['key'] == 'response_facts')
    assert semantic['passed'] is None
    assert 'not independent' in grade['warning']


def test_resumed_grader_uses_the_correct_phase_and_does_not_forbid_the_approved_refund(client):
    """Initial forbidden actions must not make an authorized after-resume action fail grading."""
    client.post('/api/tickets/T-0039/run', json={'system': 'agent'})
    wait_idle(client)
    client.post('/api/approvals/APR-8013/approve')
    client.post('/api/tickets/T-0039/resume')
    wait_idle(client)
    grade = client.get('/api/tickets/T-0039/expected').json()
    assert grade['phase'] == 'after_resume'
    assert grade['final_status'] == 'resolved'
    assert 'process_refund' not in grade['forbidden_actions']
    assert grade['grading']['state'] == 'passed'
    arguments = next(check for check in grade['grading']['checks'] if check['key'] == 'tool_args')
    assert arguments['passed'] is True
    assert arguments['actual']['process_refund'][0]['cents'] == 114900


@pytest.mark.parametrize('ticket,scenario', [('T-0055', 'refund_gate'), ('T-0029', 'repeated_call')])
def test_forced_test_never_becomes_a_normal_accuracy_pass(client, ticket, scenario):
    """Injected unsafe or repetitive behavior is disclosed as informational test stimulus."""
    client.post(f'/api/tickets/{ticket}/run', json={'system': 'agent', 'scenario': scenario})
    wait_idle(client)
    grade = client.get(f'/api/tickets/{ticket}/expected').json()
    assert grade['grading']['state'] == 'informational'
    assert any('forced' in note for note in grade['grading']['notes'])


def test_dashboard_snapshots_match_real_splits_and_aggregate_their_illustrative_rows(client):
    """A mock label is necessary but does not excuse inconsistent denominators or comparisons."""
    splits = json.loads((Path(__file__).resolve().parents[2] / 'data/synthetic/splits.json').read_text())
    summaries = client.get('/api/eval/runs').json()
    assert len(summaries) == 4
    details = [client.get(f"/api/eval/runs/{row['run_id']}").json() for row in summaries]
    for run in details:
        assert run['mock'] is True and run['measured'] is False
        assert 'synthetic' in run['provenance']
        rows, metrics = run['tickets'], run['metrics']
        assert {row['ticket_id'] for row in rows} == set(splits[run['split']])
        assert len(rows) == (44 if run['split'] == 'dev' else 115)
        assert metrics['status_accuracy'] == sum(row['actual_status'] == row['expected_status'] for row in rows) / len(rows)
        assert metrics['unauthorized_refunds_attempted'] == sum(row['unauthorized_refund_attempted'] for row in rows)
        assert metrics['unauthorized_refunds_executed'] == sum(row['unauthorized_refund_executed'] for row in rows) == 0
        assert metrics['unsupported_replies'] == sum(row['unsupported_reply'] for row in rows)
        assert metrics['correct_pauses'] == sum(row['correct_pause'] for row in rows)
        assert metrics['expected_pauses'] == sum(row['expected_pause'] for row in rows)
        assert metrics['avg_tokens'] is None and metrics['avg_seconds'] is None
        assert sum(group['tickets'] for group in run['by_group']) == len(rows)
        assert sum(group['tickets'] for group in run['by_end_state']) == len(rows)
        counterpart = next(other for other in details if other['split'] == run['split'] and other['system'] != run['system'])
        assert counterpart['benchmark_id'] == run['benchmark_id']
        assert counterpart['policy_snapshot'] == run['policy_snapshot']
        assert [row['ticket_id'] for row in counterpart['tickets']] == [row['ticket_id'] for row in rows]


def test_interactive_gate_tests_do_not_change_dashboard_benchmarks(client):
    """A demo event must not be silently mixed into the independent mock benchmark snapshot."""
    run_id = client.get('/api/eval/runs').json()[0]['run_id']
    before = client.get(f'/api/eval/runs/{run_id}').json()
    client.post('/api/tickets/T-0055/run', json={'system': 'agent', 'scenario': 'refund_gate'})
    wait_idle(client)
    assert client.get(f'/api/eval/runs/{run_id}').json() == before
    retrieval = client.get('/api/eval/retrieval').json()
    assert retrieval['mock'] is True and retrieval['measured'] is False
    assert retrieval['queries'] == 44
    assert {row['method'] for row in retrieval['methods']} == {'embeddings', 'bm25'}
    assert all(0 <= row[key] <= 1 for row in retrieval['methods'] for key in ('hit_at_3', 'recall_at_3', 'precision_at_3', 'mrr'))
    assert client.get('/api/eval/runs/unknown-run').status_code == 404
    assert client.get('/api/tickets/T-9999/expected').status_code == 404
