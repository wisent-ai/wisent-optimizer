"""Whether a journey definition can be trusted to drive a screen.

The runtime accepts a definition from the integration service, so every field
it will later read is checked here first, and a definition that fails is
refused rather than partly used: a half-valid journey shows the reader a
screen nobody designed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from .bundle import (
    FIRST_SUCCESS_FACT,
    JOURNEY_ID,
    JOURNEY_VERSION,
    PRODUCT_ID,
    SCHEMA_VERSION,
    _IDENTIFIER,
    _MAX_BUNDLE_BYTES,
    _SHA256,
    _SUPPORTED_ACTIONS,
    _SUPPORTED_FACTS,
    _SUPPORTED_OPERATORS,
    _SUPPORTED_SCREEN_KINDS,
    _UUID,
    _canonical,
    _digest,
    _fact_condition,
)

# Bounds of a journey graph the runtime will walk: condition nesting and fan-out, screens and transitions.
_MAX_CONDITION_DEPTH = 16
_MAX_CONDITION_CHILDREN = 32
_MAX_SCREENS = 128
_MAX_TRANSITIONS = 128


def _validate_condition(condition: Any, depth: int = 0) -> None:
    if depth > _MAX_CONDITION_DEPTH or not isinstance(condition, dict):
        raise ValueError("journey condition is invalid")
    kind = condition.get("kind")
    if kind in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or len(children) > _MAX_CONDITION_CHILDREN:
            raise ValueError("journey condition group is invalid")
        for child in children:
            _validate_condition(child, depth + 1)
        return
    if kind == "not":
        _validate_condition(condition.get("condition"), depth + 1)
        return
    if kind != "fact" or condition.get("fact") not in _SUPPORTED_FACTS:
        raise ValueError("journey condition fact is invalid")
    if condition.get("operator") not in _SUPPORTED_OPERATORS:
        raise ValueError("journey condition operator is invalid")
    value = condition.get("value")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("journey condition value is invalid")


def validate_bundle(bundle: Any) -> Dict[str, Any]:
    if not isinstance(bundle, dict) or len(_canonical(bundle).encode("utf-8")) > _MAX_BUNDLE_BYTES:
        raise ValueError("journey bundle envelope is invalid")
    if not _UUID.fullmatch(str(bundle.get("journey_version_id", ""))):
        raise ValueError("journey version id is invalid")
    canonical_definition = bundle.get("canonical_definition")
    content_sha256 = bundle.get("content_sha256")
    definition = bundle.get("definition")
    if not isinstance(canonical_definition, str) or not _SHA256.fullmatch(str(content_sha256 or "")):
        raise ValueError("journey bundle content is invalid")
    if not isinstance(definition, dict) or _canonical(definition) != canonical_definition:
        raise ValueError("journey canonical definition does not match")
    if _digest(canonical_definition) != content_sha256:
        raise ValueError("journey content hash does not match")
    identity = (
        definition.get("schema_version"), definition.get("product_id"),
        definition.get("journey_id"), definition.get("journey_version"),
        definition.get("first_success_fact"),
    )
    if identity != (SCHEMA_VERSION, PRODUCT_ID, JOURNEY_ID, JOURNEY_VERSION, FIRST_SUCCESS_FACT):
        raise ValueError("journey identity is invalid")
    source_revision = definition.get("source_revision")
    if not _SHA256.fullmatch(str(source_revision or "")) or bundle.get("source_revision") != source_revision:
        raise ValueError("journey source revision is invalid")
    try:
        datetime.fromisoformat(str(definition.get("published_at", "")).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("journey publication time is invalid") from error
    analytics = definition.get("analytics_contract")
    expected_analytics = {
        "contract_version": "1",
        "surface": "cli.first-use",
        "exposure_event": "onboarding_step_viewed",
        "primary_action_event": "onboarding_first_action_completed",
        "completion_event": "onboarding_completed",
        "first_success_event": "onboarding_first_success_observed",
    }
    if analytics != expected_analytics:
        raise ValueError("journey analytics contract is invalid")
    screens = definition.get("screens")
    if not isinstance(screens, list) or not screens or len(screens) > _MAX_SCREENS:
        raise ValueError("journey screen graph is invalid")
    by_id: Dict[str, Dict[str, Any]] = {}
    for screen in screens:
        if not isinstance(screen, dict):
            raise ValueError("journey screen is invalid")
        screen_id = screen.get("screen_id")
        if not isinstance(screen_id, str) or not _IDENTIFIER.fullmatch(screen_id) or screen_id in by_id:
            raise ValueError("journey screen id is invalid")
        if screen.get("screen_kind") not in _SUPPORTED_SCREEN_KINDS:
            raise ValueError("journey screen kind is unsupported")
        if (
            not isinstance(screen.get("title_key"), str)
            or not isinstance(screen.get("body_key"), str)
            or not isinstance(screen.get("required"), bool)
        ):
            raise ValueError("journey screen presentation keys are invalid")
        presentation = screen.get("presentation")
        if (
            not isinstance(presentation, dict)
            or any(not isinstance(value, (str, int, float, bool, type(None))) for value in presentation.values())
        ):
            raise ValueError("journey screen presentation is invalid")
        actions = screen.get("actions")
        if (
            not isinstance(actions, list)
            or len(actions) != len(set(actions))
            or any(action not in _SUPPORTED_ACTIONS for action in actions)
        ):
            raise ValueError("journey screen action is unsupported")
        transitions = screen.get("transitions")
        if not isinstance(transitions, list) or len(transitions) > _MAX_TRANSITIONS:
            raise ValueError("journey transitions are invalid")
        for condition_name in ("entry_conditions", "completion_evidence"):
            if screen.get(condition_name) is not None:
                _validate_condition(screen[condition_name])
        by_id[screen_id] = screen
    if definition.get("entry_screen_id") not in by_id:
        raise ValueError("journey entry screen is missing")
    for screen in screens:
        fallback = screen.get("fallback_screen_id")
        if fallback is not None and fallback not in by_id:
            raise ValueError("journey fallback screen is missing")
        for transition in screen["transitions"]:
            if not isinstance(transition, dict) or transition.get("next_screen_id") not in by_id:
                raise ValueError("journey transition target is missing")
            priority = transition.get("priority")
            reason_code = transition.get("reason_code")
            if (
                not isinstance(priority, int)
                or isinstance(priority, bool)
                or priority < 0
                or not isinstance(reason_code, str)
                or not _IDENTIFIER.fullmatch(reason_code)
            ):
                raise ValueError("journey transition is invalid")
            if transition.get("condition") is not None:
                _validate_condition(transition["condition"])
    kinds = [screen["screen_kind"] for screen in screens]
    if len(screens) != len(_SUPPORTED_SCREEN_KINDS) or set(kinds) != _SUPPORTED_SCREEN_KINDS:
        raise ValueError("journey must contain each product-owned screen exactly once")
    ranked_screen = next(screen for screen in screens if screen["screen_kind"] == "optimizer_ranked_result")
    if ranked_screen["transitions"] or ranked_screen.get("completion_evidence") != _fact_condition(FIRST_SUCCESS_FACT):
        raise ValueError("ranked result must be the terminal first-success screen")
    if any(
        not screen["transitions"] and not screen.get("fallback_screen_id")
        for screen in screens
        if screen is not ranked_screen
    ):
        raise ValueError("only the ranked result may be terminal")
    reachable = {definition["entry_screen_id"]}
    pending = [definition["entry_screen_id"]]
    while pending:
        screen = by_id[pending.pop()]
        targets = [transition["next_screen_id"] for transition in screen["transitions"]]
        if screen.get("fallback_screen_id"):
            targets.append(screen["fallback_screen_id"])
        for target in targets:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    if reachable != set(by_id):
        raise ValueError("journey contains unreachable screens")
    return bundle

