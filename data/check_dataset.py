"""Consistency checks: every expected label must be justified by the store records.

Usage (from the repo root):  python data/check_dataset.py
Exits non-zero and lists problems if any check fails.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "synthetic"
STATUSES = {"resolved", "closed", "awaiting_customer", "awaiting_approval", "escalated", "failed_safe"}
LABEL_ONLY = {"category", "team", "final_status", "policy_ids", "expected_actions", "scenario_type"}


def jsonl(name):
    return [json.loads(line) for line in (OUT / name).read_text().splitlines()]


def main():
    tickets, labels, prov = jsonl("tickets.jsonl"), jsonl("labels.jsonl"), jsonl("provenance.jsonl")
    store = json.loads((OUT / "store.json").read_text())
    splits = json.loads((OUT / "splits.json").read_text())
    staged = {a["approval_id"]: a for a in json.loads((OUT / "demo_staged_approval.json").read_text())}
    policies = json.loads((OUT / "policy_ids.json").read_text())

    orders = {o["order_id"]: o for o in store["orders"]}
    customers = {c["customer_id"]: c for c in store["customers"]}
    approvals = {a["approval_id"]: a for a in store["approvals"]}
    refunded = {}
    for r in store["refunds"]:
        refunded[r["order_id"]] = refunded.get(r["order_id"], 0) + r["amount_cents"]
    lab_by = {lab["ticket_id"]: lab for lab in labels}
    errs = []

    def err(tid, msg):
        errs.append(f"{tid}: {msg}")

    ids = [t["ticket_id"] for t in tickets]
    if len(set(ids)) != len(ids):
        errs.append("duplicate ticket ids")
    if set(ids) != set(lab_by) or set(ids) != {p["ticket_id"] for p in prov}:
        errs.append("tickets, labels and provenance do not cover the same ticket ids")
    if sorted(splits["dev"] + splits["test"] + splits["demo"]) != sorted(ids):
        errs.append("splits do not partition the ticket ids exactly once")
    if len(set(approvals) & set(staged)):
        errs.append("staged approvals already present in the store")

    def check_refund(tid, args, pool):
        a = pool.get(args["approval_id"])
        if a is None or a["order_id"] != args["order_id"] or a["amount_cents"] != args["cents"]:
            err(tid, "expected refund has no matching approval")
        o = orders.get(args["order_id"])
        if o is None or o["return"] is None:
            err(tid, "expected refund but return not received")
        elif args["cents"] > o["total_cents"] - refunded.get(o["order_id"], 0):
            err(tid, "expected refund exceeds remaining balance")

    for t in tickets:
        tid, lab = t["ticket_id"], lab_by.get(t["ticket_id"])
        if lab is None:
            continue
        if LABEL_ONLY & set(t):
            err(tid, f"label fields leaked into ticket: {sorted(LABEL_ONLY & set(t))}")
        if lab["final_status"] not in STATUSES:
            err(tid, f"unknown status {lab['final_status']}")
        unknown = set(lab["policy_ids"]) - set(policies)
        if unknown:
            err(tid, f"unknown policy ids {sorted(unknown)}")
        cust = customers.get(t["customer_id"])
        if cust is None or cust["email"] != t["customer_email"]:
            err(tid, "customer missing or email mismatch")
        if tid not in store["ticket_state"]:
            err(tid, "no ticket_state record")

        customer_text = " ".join(m["body"] for m in t["messages"] if m["role"] == "customer")
        for oid in t["order_ids"]:
            o = orders.get(oid)
            if o is None:
                err(tid, f"order {oid} not in store")
            elif (o["customer_id"] != t["customer_id"]) != bool(lab.get("scope_violation")):
                err(tid, f"order {oid} ownership does not match scope_violation label")
            if oid not in customer_text:
                err(tid, f"order {oid} listed but never mentioned by the customer")

        if lab["needs_order"] and not t["order_ids"] and lab["final_status"] not in ("awaiting_customer", "failed_safe"):
            err(tid, "request needs an order but none given: must end awaiting_customer")

        acts, forb = set(lab["expected_actions"]), set(lab["forbidden_actions"])
        if acts & forb:
            err(tid, f"action both expected and forbidden: {sorted(acts & forb)}")

        args = lab["expected_tool_args"].get("process_refund")
        if args:
            check_refund(tid, args, approvals)
        elif "process_refund" in acts:
            err(tid, "process_refund expected without tool args")

        if lab["final_status"] == "awaiting_approval":
            for oid in t["order_ids"]:
                o = orders[oid]
                if o["return"] and any(a["order_id"] == oid and a["amount_cents"] == o["total_cents"]
                                       for a in approvals.values()):
                    err(tid, f"awaiting_approval but a matching approval for {oid} already exists")

        cargs = lab["expected_tool_args"].get("cancel_order")
        if cargs:
            o = orders.get(cargs["order_id"])
            if o is None or o["status"] != "processing" or o["shipment"] is not None:
                err(tid, "expected cancel on an order that is not unshipped/processing")
        elif "cancel_order" in acts:
            err(tid, "cancel_order expected without tool args")

        if lab["final_status"] == "closed" and (len(t["messages"]) < 2 or t["messages"][-1]["role"] != "customer"):
            err(tid, "closed requires a final customer confirmation message")
        if lab["final_status"] == "failed_safe" and "failure_injection" not in t:
            err(tid, "failed_safe without an injected failure")

        resume = lab.get("after_resume")
        if resume:
            if resume["staged_approval_id"] not in staged:
                err(tid, "after_resume approval missing from the staged file")
            check_refund(tid, resume["expected_tool_args"]["process_refund"], staged)

    if errs:
        print("\n".join(errs))
        sys.exit(1)
    print(f"ok: {len(tickets)} tickets, {len(store['orders'])} orders, "
          f"{len(approvals)} approvals (+{len(staged)} staged), {len(store['refunds'])} refunds")


if __name__ == "__main__":
    main()
