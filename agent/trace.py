"""JSONL traces: one line per event, one file per ticket under runtime/traces/. Never secrets or card numbers."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .tools import CARD_NUMBER


def redact(value):
    """Mask anything that looks like a card number, at any depth of a dict/list."""
    if isinstance(value, str):
        return CARD_NUMBER.sub("[card number removed]", value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class Trace:
    """Append-only event log for one ticket; every run of that ticket (first run, resume) adds to it."""

    def __init__(self, root, ticket_id, run_id):
        self.path = Path(root) / "traces" / f"{ticket_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ticket_id, self.run_id = ticket_id, run_id

    def log(self, event, **fields):
        """Write one event, e.g. log("tool_result", tool="process_refund", ok=True, ...)."""
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ticket_id": self.ticket_id,
                "run_id": self.run_id, "event": event, **redact(fields)}
        with self.path.open("a") as f:
            f.write(json.dumps(line) + "\n")
