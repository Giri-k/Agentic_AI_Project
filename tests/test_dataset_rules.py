"""The tools agree with every label: expected refunds pass, refunds the labels forbid can't happen."""

import pytest

from agent.schemas import ToolError
from agent.staff import approve, main
from agent.tools import Context, ProcessRefundArgs, process_refund
from conftest import LABELS


def try_refund(store, ticket_id, order_id, cents, approval_id):
    """True if process_refund accepts this refund for this ticket's customer."""
    ctx = Context(ticket_id=ticket_id, customer_id=store.tickets[ticket_id]["customer_id"])
    try:
        process_refund(store, ctx, ProcessRefundArgs(order_id=order_id, cents=cents, approval_id=approval_id))
        return True
    except ToolError:
        return False


@pytest.mark.parametrize("ticket_id", [t for t, lab in LABELS.items() if "process_refund" in lab["expected_tool_args"]])
def test_every_expected_refund_passes_the_tool_checks(store, ticket_id):
    a = LABELS[ticket_id]["expected_tool_args"]["process_refund"]
    assert try_refund(store, ticket_id, a["order_id"], a["cents"], a["approval_id"])


@pytest.mark.parametrize("ticket_id", [t for t, lab in LABELS.items() if lab.get("after_resume")])
def test_paused_refunds_pass_only_after_staff_approve(store, ticket_id):
    resume = LABELS[ticket_id]["after_resume"]
    a = resume["expected_tool_args"]["process_refund"]
    assert not try_refund(store, ticket_id, a["order_id"], a["cents"], a["approval_id"])
    approve(store, resume["staged_approval_id"])
    assert try_refund(store, ticket_id, a["order_id"], a["cents"], a["approval_id"])


# Team decision: an approval whose amount contradicts its own reason is caught by the model, not the tool.
MODEL_GUARDED = [t for t, lab in LABELS.items() if "approval amount is half the requested refund" in lab["unresolved_requirements"]]
PYTHON_GUARDED = [t for t, lab in LABELS.items() if "process_refund" in lab["forbidden_actions"] and t not in MODEL_GUARDED]


@pytest.mark.parametrize("ticket_id", PYTHON_GUARDED)
def test_no_refund_is_possible_where_labels_forbid_one(store, ticket_id):
    """Try every approval in the store, at its own amount, on every order the ticket names."""
    for order_id in store.tickets[ticket_id]["order_ids"]:
        for a in store.data["approvals"]:
            assert not try_refund(store, ticket_id, order_id, a["amount_cents"], a["approval_id"]), a["approval_id"]


@pytest.mark.parametrize("ticket_id", MODEL_GUARDED)
def test_contradictory_approvals_are_left_to_the_model(store, ticket_id):
    """Documents the gap: the tool accepts these half-amount refunds, so only the model's escalation prevents them."""
    order_id = store.tickets[ticket_id]["order_ids"][0]
    approval = store.approvals(order_id)[0]
    assert approval["amount_cents"] == store.order(order_id)["total_cents"] // 2
    assert try_refund(store, ticket_id, order_id, approval["amount_cents"], approval["approval_id"])


def test_staff_cli_reset_and_approve(tmp_path, capsys):
    assert main(["--runtime", str(tmp_path), "reset"]) == 0
    assert main(["--runtime", str(tmp_path), "approve", "APR-8013"]) == 0
    assert main(["--runtime", str(tmp_path), "approve", "APR-0000"]) == 1
    assert "approved APR-8013" in capsys.readouterr().out
