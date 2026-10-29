"""Working store for agent runs: a copy of data/synthetic/store.json under runtime/.

Every successful action is saved immediately (temp file, then rename), so a receipt
survives a crash and a resumed run always rereads current records. Ticket text is
read from data/synthetic/tickets.jsonl and never changes.
"""

import json
import os
import tempfile
from pathlib import Path

from .config import DATA, RUNTIME

ID_PREFIX = {"refund": ("RF-", 4000), "message": ("MSG-", 9000), "cancellation": ("CAN-", 7000),
             "audit": ("AUD-", 5000)}
COMMITTED_REFUNDS = {"issued", "settled"}


def load_jsonl(path):
    """Read a .jsonl file: one JSON object per line."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def atomic_write(path, text):
    """Write to a temp file, then rename over the target, so a crash never leaves a half-written file."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _counters(data):
    """Highest ID already used per kind, so new receipts (RF-, MSG-, ...) never collide with dataset ones."""
    seen = [r["refund_id"] for r in data["refunds"]]
    seen += [a["receipt_id"] for state in data["ticket_state"].values() for a in state["actions"]]
    counters = {}
    for kind, (prefix, start) in ID_PREFIX.items():
        nums = [int(i[len(prefix):]) for i in seen if i.startswith(prefix)]
        counters[kind] = max(nums, default=start)
    return counters


class Store:
    """The fake store's records (orders, payments, approvals, refunds, ticket states) for one runtime folder."""

    def __init__(self, root=RUNTIME):
        self.root = Path(root)
        self.path = self.root / "store.json"
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} is missing; run `python -m agent.staff reset` first")
        self.data = json.loads(self.path.read_text())
        self.tickets = {t["ticket_id"]: t for t in load_jsonl(DATA / "tickets.jsonl")}
        self.policy_ids = set(json.loads((DATA / "policy_ids.json").read_text()))

    @classmethod
    def reset(cls, root=RUNTIME):
        """Start a fresh working store from the dataset. Removes only files this module writes."""
        root = Path(root)
        (root / "checkpoints").mkdir(parents=True, exist_ok=True)
        (root / "traces").mkdir(exist_ok=True)
        (root / ".gitignore").write_text("*\n")
        for old in [root / "outbox.jsonl", *(root / "checkpoints").glob("*.json"), *(root / "traces").glob("*.jsonl")]:
            old.unlink(missing_ok=True)
        data = json.loads((DATA / "store.json").read_text())
        data.update(receipts={}, outbox=[], cancellations=[], audit=[], counters=_counters(data))
        atomic_write(root / "store.json", json.dumps(data, indent=1))
        return cls(root)

    def save(self):
        """Persist everything; outbox.jsonl is a readable copy of the sent messages."""
        atomic_write(self.path, json.dumps(self.data, indent=1))
        atomic_write(self.root / "outbox.jsonl", "".join(json.dumps(m) + "\n" for m in self.data["outbox"]))

    def next_id(self, kind):
        """Allocate the next receipt ID of a kind, e.g. next_id("refund") -> "RF-4019"."""
        prefix, _ = ID_PREFIX[kind]
        self.data["counters"][kind] += 1
        return f"{prefix}{self.data['counters'][kind]}"

    @property
    def as_of(self):
        """The store's 'today' (2026-10-20); all date rules use this, not the real clock."""
        return self.data["as_of"]

    # --- lookups -------------------------------------------------------------

    def order(self, order_id):
        """One order by ID, or None."""
        return next((o for o in self.data["orders"] if o["order_id"] == order_id), None)

    def customer(self, customer_id):
        """One customer record (name, email)."""
        return next(c for c in self.data["customers"] if c["customer_id"] == customer_id)

    def payments(self, order_id):
        """All payment records for an order (a duplicate charge shows up as two)."""
        return [p for p in self.data["payments"] if p["order_id"] == order_id]

    def approvals(self, order_id):
        """Staff approvals in the active store for an order (staged demo approvals are not here)."""
        return [a for a in self.data["approvals"] if a["order_id"] == order_id]

    def approval(self, approval_id):
        """One active approval by ID, or None."""
        return next((a for a in self.data["approvals"] if a["approval_id"] == approval_id), None)

    def refunds(self, order_id=None, approval_id=None):
        """Refunds filtered by order and/or approval."""
        return [r for r in self.data["refunds"]
                if (order_id is None or r["order_id"] == order_id)
                and (approval_id is None or r["approval_id"] == approval_id)]

    def captured_cents(self, order_id):
        """Money actually taken for an order."""
        return sum(p["amount_cents"] for p in self.payments(order_id) if p["status"] == "captured")

    def remaining_refundable_cents(self, order_id):
        """POL-RET-06: captured payments minus refunds already issued or settled."""
        refunded = sum(r["amount_cents"] for r in self.refunds(order_id) if r["status"] in COMMITTED_REFUNDS)
        return self.captured_cents(order_id) - refunded

    def ticket_state(self, ticket_id):
        """Saved status, version and action receipts for a ticket."""
        return self.data["ticket_state"][ticket_id]

    def ticket_for_agent(self, ticket_id):
        """Ticket as the agent may see it: injected failures are harness-only."""
        return {k: v for k, v in self.tickets[ticket_id].items() if k != "failure_injection"}

    def failure_injection(self, ticket_id):
        """The test failure planned for this ticket (e.g. a timeout), read only by the dispatcher."""
        return self.tickets[ticket_id].get("failure_injection")
