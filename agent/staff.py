"""Stand-in for human staff. These actions are never agent tools, so the model can't approve its own refunds.

Usage (from the repo root):
  uv run python -m agent.staff reset              fresh working store in runtime/
  uv run python -m agent.staff approve APR-8013   staff approves a staged refund
  uv run python -m agent.staff status T-0039      show a ticket's saved state
"""

import argparse
import json
import sys

from .config import DATA, RUNTIME
from .store import Store
from .trace import Trace


def staged_approvals():
    """Approvals staff 'add' during the demo; inactive until approve() copies them into the store."""
    return {a["approval_id"]: a for a in json.loads((DATA / "demo_staged_approval.json").read_text())}


def approve(store, approval_id):
    """Copy one staged approval into the active store and trace it on the ticket it unblocks. Safe to repeat."""
    staged = staged_approvals().get(approval_id)
    if staged is None:
        raise ValueError(f"{approval_id} is not a staged approval")
    if store.approval(approval_id) is None:
        store.data["approvals"].append(staged)
        store.save()
    for ticket_id, ticket in store.tickets.items():
        if staged["order_id"] in ticket["order_ids"]:
            Trace(store.root, ticket_id, "staff").log("staff_approval", approval=staged)
    return staged


def main(argv=None):
    """Command-line entry point for the three staff actions."""
    parser = argparse.ArgumentParser(prog="python -m agent.staff", description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(RUNTIME), help="working store folder (default: runtime/)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset", help="start a fresh working store from the dataset")
    sub.add_parser("approve", help="add a staged approval to the store").add_argument("approval_id")
    sub.add_parser("status", help="show a ticket's saved state").add_argument("ticket_id")
    args = parser.parse_args(argv)

    if args.command == "reset":
        store = Store.reset(args.runtime)
        print(f"fresh store in {store.root}: {len(store.data['orders'])} orders, {len(store.data['approvals'])} approvals")
        return 0
    store = Store(args.runtime)
    if args.command == "approve":
        try:
            a = approve(store, args.approval_id)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"approved {a['approval_id']}: order {a['order_id']}, {a['amount_cents']} cents, by {a['approved_by']}")
    else:
        print(json.dumps(store.ticket_state(args.ticket_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
