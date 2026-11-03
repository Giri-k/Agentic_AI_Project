"""Durable execution progress, saved atomically with the store's action receipts."""

from copy import deepcopy


def load_progress(store, ticket_id, system=None):
    """Read a ticket's persisted counters/checkpoint without sharing mutable state."""
    progress = store.data.get("execution", {}).get(ticket_id)
    if not progress or (system is not None and progress.get("system") != system):
        return None
    return deepcopy(progress)


def save_progress(store, ticket_id, system, budget, *, run_id, mode, in_progress, checkpoint=None):
    """Commit counters before a call, and alongside receipts after a mutating tool.

    The checkpoint file remains a readable compatibility copy. This record in store.json
    is authoritative, so a crash cannot restore an older budget after an action committed.
    """
    progress = {"system": system, "budget": budget.to_dict(), "run_id": run_id,
                "mode": mode, "in_progress": in_progress}
    if checkpoint is not None:
        progress["checkpoint"] = deepcopy(checkpoint)
    store.data.setdefault("execution", {})[ticket_id] = progress
    store.save()
    return progress
