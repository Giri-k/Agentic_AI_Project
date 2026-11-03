"""Deterministic dashboard illustrations, explicitly separate from measured evaluation runs."""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from agent.eval_run import group_of

PROVENANCE = (
    "Illustrative MOCK DATA generated deterministically from draft labels and split membership. "
    "Predictions, safety flags and call counts are synthetic UI fixtures, not measured agent or baseline performance. "
    "No models, retrieval services or evaluation harness were run."
)


def _digest(value: Any) -> str:
    """Give matched mock systems the same stable benchmark and policy identifiers."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def _row(label: dict[str, Any], index: int, system: str) -> dict[str, Any]:
    """Create one explicit synthetic observation; all aggregates derive from these rows."""
    expected = label["final_status"]
    mismatch = index % (11 if system == "agent" else 5) == 0
    actual = ("failed_safe" if expected == "escalated" else "escalated") if mismatch else expected
    expected_policies = label["policy_ids"]
    cited = expected_policies[:-1] if index % (13 if system == "agent" else 7) == 0 else expected_policies[:]
    expected_pause = expected in {"awaiting_customer", "awaiting_approval"}
    attempted = bool(label.get("refund_risk")) and index % (17 if system == "agent" else 7) == 0
    unsupported = index % (19 if system == "agent" else 9) == 0
    return {
        "ticket_id": label["ticket_id"], "scenario_type": label["scenario_type"], "group": group_of(label),
        "expected_status": expected, "actual_status": actual,
        "passed": actual == expected and not unsupported and set(cited) == set(expected_policies),
        "notes": ["Illustrative row only; no executed run or historical trace exists for this benchmark snapshot."]
                 + (["Synthetic final-state mismatch."] if mismatch else [])
                 + (["Synthetic unsupported-reply flag."] if unsupported else [])
                 + (["Synthetic missing citation."] if len(cited) != len(expected_policies) else []),
        "trace_available": False, "refund_risk": bool(label.get("refund_risk")),
        "unauthorized_refund_attempted": attempted, "unauthorized_refund_executed": False,
        "unsupported_reply": unsupported, "expected_pause": expected_pause,
        "correct_pause": expected_pause and actual == expected,
        "expected_policy_ids": expected_policies[:], "actual_policy_ids": cited,
        "citation_matched": len(set(cited) & set(expected_policies)),
        "citation_retrieved": len(cited), "citation_relevant": len(expected_policies),
        "model_calls": 3 if system == "agent" else 1,
        "tool_calls": len(label["expected_actions"]) + (1 if attempted else 0),
    }


def _aggregate(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Group accuracy by a row field without inventing totals unrelated to the observations."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    return [
        {"group" if field == "group" else "status": name, "tickets": len(items),
         "status_accuracy": sum(row["actual_status"] == row["expected_status"] for row in items) / len(items)}
        for name, items in sorted(groups.items())
    ]


def build_eval_runs(labels: dict[str, dict[str, Any]], splits: dict[str, str],
                    policies: dict[str, dict[str, Any]], as_of: str) -> dict[str, dict[str, Any]]:
    """Prepare immutable agent/baseline illustrations for identical dev and test snapshots."""
    policy_snapshot = f"policies-{_digest(policies)}"
    runs = {}
    for split in ("dev", "test"):
        selected = [labels[ticket_id] for ticket_id in sorted(labels) if splits[ticket_id] == split]
        benchmark_id = f"mock-{split}-{_digest(selected)}"
        for system in ("agent", "baseline"):
            rows = [_row(label, index, system) for index, label in enumerate(selected)]
            count = len(rows)
            matched = sum(row["citation_matched"] for row in rows)
            retrieved = sum(row["citation_retrieved"] for row in rows)
            relevant = sum(row["citation_relevant"] for row in rows)
            run_id = f"mock-{system}-{split}-v1"
            runs[run_id] = {
                "run_id": run_id, "system": system, "split": split,
                "created_at": f"{as_of}T00:00:00Z", "mock": True, "measured": False,
                "benchmark_id": benchmark_id, "policy_snapshot": policy_snapshot,
                "provenance": PROVENANCE,
                "metrics": {
                    "status_accuracy": sum(row["actual_status"] == row["expected_status"] for row in rows) / count if count else None,
                    "unauthorized_refunds_attempted": sum(row["unauthorized_refund_attempted"] for row in rows),
                    "unauthorized_refunds_executed": sum(row["unauthorized_refund_executed"] for row in rows),
                    "refund_risk_tickets": sum(row["refund_risk"] for row in rows),
                    "unsupported_replies": sum(row["unsupported_reply"] for row in rows),
                    "correct_pauses": sum(row["correct_pause"] for row in rows),
                    "expected_pauses": sum(row["expected_pause"] for row in rows),
                    "citation_precision": matched / retrieved if retrieved else None,
                    "citation_recall": matched / relevant if relevant else None,
                    "avg_model_calls": sum(row["model_calls"] for row in rows) / count if count else None,
                    "avg_tool_calls": sum(row["tool_calls"] for row in rows) / count if count else None,
                    "avg_tokens": None, "avg_seconds": None,
                },
                "by_group": _aggregate(rows, "group"), "by_end_state": _aggregate(rows, "expected_status"),
                "tickets": rows,
            }
    return runs


def eval_summaries(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep comparison provenance available before downloading full per-ticket rows."""
    fields = ("run_id", "system", "split", "created_at", "mock", "measured", "benchmark_id", "policy_snapshot", "provenance")
    return [{**{key: run[key] for key in fields}, "tickets": len(run["tickets"])} for run in runs.values()]


def build_retrieval_metrics(labels: dict[str, dict[str, Any]], splits: dict[str, str],
                            policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compute illustrative ranking metrics from deterministic mock rankings, never real embeddings."""
    selected = [labels[ticket_id] for ticket_id in sorted(labels) if splits[ticket_id] == "dev"]
    methods = []
    for method, miss_period in (("embeddings", 9), ("bm25", 5)):
        hits, recall, precision, reciprocal_rank = 0, 0.0, 0.0, 0.0
        for index, label in enumerate(selected):
            expected = set(label["policy_ids"])
            unrelated = [policy_id for policy_id in sorted(policies) if policy_id not in expected]
            ranked = unrelated[:3] if index % miss_period == 0 else list(label["policy_ids"][:2]) + unrelated[:3]
            if index % 4 == 1:
                ranked = unrelated[:1] + ranked
            ranked = ranked[:3]
            found = len(set(ranked) & expected)
            hits += bool(found)
            recall += found / len(expected) if expected else 0
            precision += found / len(ranked) if ranked else 0
            reciprocal_rank += next((1 / rank for rank, policy_id in enumerate(ranked, 1) if policy_id in expected), 0)
        count = len(selected)
        methods.append({"method": method, "hit_at_3": hits / count if count else None,
                        "recall_at_3": recall / count if count else None,
                        "precision_at_3": precision / count if count else None,
                        "mrr": reciprocal_rank / count if count else None})
    return deepcopy({"mock": True, "measured": False, "source": "Deterministic mock ranking fixtures",
                     "provenance": PROVENANCE, "note": "These are illustrative rankings, not verified embeddings or BM25 measurements.",
                     "split": "dev", "tickets": len(selected), "queries": len(selected), "methods": methods})


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
