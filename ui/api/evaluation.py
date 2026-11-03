"""Measured evaluation results for the console, as written by agent.eval_run and agent.eval_retrieval."""

import json
from pathlib import Path
from typing import Any


def eval_summaries(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Provenance and headline metrics for every run, without the per-ticket rows (for the all-runs table)."""
    fields = ("run_id", "system", "split", "created_at", "measured", "benchmark_id", "policy_snapshot", "provenance")
    return [{**{key: run[key] for key in fields}, "tickets": len(run["tickets"]), "metrics": run["metrics"],
             "settings": run.get("settings", {})} for run in runs.values()]


def load_measured(results_dir) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Measured runs and retrieval metrics written by agent.eval_run / agent.eval_retrieval, if any exist."""
    runs, retrieval = {}, None
    for path in sorted(Path(results_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue  # a file being written right now
        if path.name == "retrieval.json":
            retrieval = data
        elif data.get("measured") and "run_id" in data:
            runs[data["run_id"]] = data
    return runs, retrieval
