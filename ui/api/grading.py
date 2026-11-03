"""The optional grader: structural checks of a ticket's latest run against its label, never an accuracy claim."""

import json
from copy import deepcopy
from typing import Any

from agent.eval_run import action_present

WARNING = ("These checks are structural; whether the reply's meaning and grounding are right still needs review.")


def expected_outcome(label: dict[str, Any], state: dict[str, Any], events: list[dict[str, Any]],
                     execution: dict[str, Any] | None, running: bool) -> dict[str, Any]:
    """Compare saved observations with the correct annotation phase and disclose unverified claims."""
    expected = deepcopy(label)
    resumed = bool(execution and execution.get("last_mode") == "resume" and label.get("after_resume"))
    if resumed:
        expected.update(deepcopy(label["after_resume"]))
        permitted = {action.split("(", 1)[0] for action in expected["expected_actions"]}
        expected["forbidden_actions"] = [action for action in label["forbidden_actions"] if action not in permitted]
        expected["response_facts"] = []
        expected["unresolved_requirements"] = [] if expected["final_status"] in {"resolved", "closed"} else expected.get("unresolved_requirements", [])
    result = state.get("last_result")
    completed = bool(execution and result and not running)
    latest = [event for event in events if execution and event["run_id"] == execution.get("last_run_id")]
    requests = [event for event in latest if event["event"] == "tool_request"]
    observed = [event["tool"] for event in requests]
    checks: list[dict[str, Any]] = []

    def check(key: str, title: str, wanted: Any, actual: Any, passed: bool | None, note: str | None = None):
        """Hide provisional judgments until a run has a saved final result."""
        checks.append({"key": key, "label": title, "expected": wanted, "actual": actual,
                       "passed": passed if completed else None, **({"note": note} if note else {})})

    check("status", "Final status", expected["final_status"], result.get("status") if result else None, bool(result and result["status"] == expected["final_status"]))
    check("category", "Category", expected["category"], result.get("category") if result else None, bool(result and result["category"] == expected["category"]))
    check("team", "Team", expected["team"], state.get("team"), state.get("team") == expected["team"])
    actual_citations = result.get("citations", []) if result else []
    check("citations", "Policy citations", expected["policy_ids"], actual_citations, set(expected["policy_ids"]) == set(actual_citations))
    missing = [action for action in expected["expected_actions"] if not action_present(action, latest)]
    check("expected_actions", "Expected actions", expected["expected_actions"], observed, not missing,
          f"Missing: {', '.join(missing)}" if missing else "Requests are checked in this invocation, including annotated retry/fallback evidence.")
    forbidden = [action for action in expected["forbidden_actions"] if action.split("(", 1)[0] in observed]
    check("forbidden_actions", "Forbidden actions avoided", expected["forbidden_actions"], forbidden, not forbidden)
    actual_args = {}
    args_ok = True
    for tool, wanted in expected.get("expected_tool_args", {}).items():
        candidates = []
        for event in requests:
            if event["tool"] == tool:
                try:
                    candidates.append(json.loads(event["args"]) if isinstance(event["args"], str) else event["args"])
                except (ValueError, TypeError):
                    continue
        actual_args[tool] = candidates
        args_ok = args_ok and any(isinstance(candidate, dict) and all(candidate.get(key) == value for key, value in wanted.items()) for candidate in candidates)
    check("tool_args", "Required tool arguments", expected.get("expected_tool_args", {}), actual_args, args_ok)
    check("response_facts", "Response facts", expected.get("response_facts", []), result.get("response") if result else None,
          None, "Not semantically verified. Review the reply against the policy, records and receipts.")
    grade_state = ("running" if running else "not_run" if not completed
                   else "failed" if any(item["passed"] is False for item in checks) else "passed")
    notes = ["Only structural checks are automated; response meaning and factual grounding remain unverified."]
    if resumed:
        notes.append("Checks use the after-resume annotation and the latest resumed invocation. Resume-specific response facts have not been annotated.")
    expected.update(phase="after_resume" if resumed else "initial", source="labels", warning=WARNING,
                    grading={"state": grade_state, "checks": checks, "notes": notes})
    return deepcopy(expected)
