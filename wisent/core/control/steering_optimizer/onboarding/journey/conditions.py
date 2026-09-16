"""Reading one condition against the evidence, and picking the next screen.

The two halves of the journey's own logic: whether a recorded fact satisfies a
condition, and which screen that makes current. Kept away from the runtime so
the question "why did it show me this screen" is answered by eighty lines
rather than two hundred.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def _evaluate(condition: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    kind = condition.get("kind")
    if kind == "all":
        return all(_evaluate(child, evidence) for child in condition.get("conditions", []))
    if kind == "any":
        return any(_evaluate(child, evidence) for child in condition.get("conditions", []))
    if kind == "not":
        return not _evaluate(condition.get("condition", {}), evidence)
    fact = condition.get("fact")
    actual = evidence.get(fact)
    operator = condition.get("operator")
    present = fact in evidence and actual is not None
    expected = condition.get("value")
    if operator == "present":
        return present
    if operator == "absent":
        return not present
    if operator == "eq":
        return actual == expected and type(actual) is type(expected)
    if operator == "not_eq":
        return actual != expected or type(actual) is not type(expected)
    if operator == "contains":
        return isinstance(actual, list) and expected in actual
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return False
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    return False


def _select_next(definition: Mapping[str, Any], current_id: str, evidence: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    screens = {screen["screen_id"]: screen for screen in definition["screens"]}
    current = screens[current_id]
    completion = current.get("completion_evidence")
    if completion and not _evaluate(completion, evidence):
        return None
    for transition in sorted(current["transitions"], key=lambda item: item["priority"]):
        condition = transition.get("condition")
        target = screens[transition["next_screen_id"]]
        entry = target.get("entry_conditions")
        if condition and not _evaluate(condition, evidence):
            continue
        if entry and not _evaluate(entry, evidence):
            continue
        return {
            "selected_next_screen_id": transition["next_screen_id"],
            "reason_code": transition["reason_code"],
        }
    fallback_id = current.get("fallback_screen_id")
    if fallback_id:
        return {"selected_next_screen_id": fallback_id, "reason_code": "fallback_evidence_unavailable"}
    return None


_RENDERERS = {
    "optimizer_search_space": (
        "1/4  Define the search space",
        "Compare deliberate candidates: steering methods, model layers, and strengths. "
        "Every added value multiplies the configurations evaluated, so start bounded and expand only when the ranking warrants it.",
    ),
    "optimizer_objective": (
        "2/4  Keep one objective",
        "Choose the task and score that represent the behavior you want. Compare every candidate on the same data and split; "
        "the ranked score is evidence for this objective, not a universal model-quality claim.",
    ),
    "optimizer_guardrails": (
        "3/4  Set guardrails before compute",
        "Bound samples, layers, strengths, and max time. Keep a baseline, inspect failures, and do not treat a submitted job or saved config as success. "
        "First success requires ranked output produced by the optimizer.",
    ),
    "optimizer_ranked_result": (
        "4/4  Produce a ranked configuration",
        "Run run_steering_optimization with method_comparison, comprehensive, or auto search. "
        "This journey completes only when that real result path returns a non-empty method ranking or scored grid-search ranking.",
    ),
}


