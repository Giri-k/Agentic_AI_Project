"""Shared test fixtures: a fresh working store per test, and the dataset labels for picking tickets."""

import json

import pytest

from agent.config import DATA
from agent.store import Store

LABELS = {json.loads(line)["ticket_id"]: json.loads(line) for line in (DATA / "labels.jsonl").read_text().splitlines()}


def tickets_of(scenario):
    """Ticket IDs of one scenario type, e.g. tickets_of("C1"). Labels are used only to choose test cases."""
    return [tid for tid, lab in LABELS.items() if lab["scenario_type"] == scenario]


@pytest.fixture
def store(tmp_path):
    """A fresh copy of the dataset store in a temp folder; tests never touch runtime/."""
    return Store.reset(tmp_path)


def args(**kw):
    """Tool arguments as the model would send them: a JSON string."""
    return json.dumps(kw)
