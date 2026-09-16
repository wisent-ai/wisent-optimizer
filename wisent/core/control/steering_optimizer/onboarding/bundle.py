"""The journey this product ships with, and the names everything else uses.

One screen is one decision the reader makes, and `CANONICAL_BUNDLE` is the
whole first-use journey as it leaves this repository: the definition the
runtime uses when the integration service has none to hand out. Its old name
named a behaviour this workshop forbids; the new one says what it is — the
journey we ship.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

PRODUCT_ID = "wisent-optimizer"
JOURNEY_ID = "first-use"
JOURNEY_VERSION = "2026-08-04.1"
SCHEMA_VERSION = 1
FIRST_SUCCESS_FACT = "ranked_configuration_observed"
CLIENT_ID = "wisent-optimizer"
TOKEN_ENV = "WISENT_OPTIMIZER_STADO_INTEGRATION_TOKEN"
BASE_URL_ENV = "STADO_INTEGRATION_API_URL"
_SHIPPED_VERSION_ID = "7d4ad04d-8301-4b1e-9e38-d7eb27bf693f"
_MAX_BUNDLE_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SCREEN_KINDS = {
    "optimizer_search_space",
    "optimizer_objective",
    "optimizer_guardrails",
    "optimizer_ranked_result",
}
_SUPPORTED_ACTIONS = {"continue", "run_optimization", "not_now"}
_SUPPORTED_FACTS = {
    "search_space_acknowledged",
    "objective_acknowledged",
    "guardrails_acknowledged",
    FIRST_SUCCESS_FACT,
}
_SUPPORTED_OPERATORS = {
    "present", "absent", "eq", "not_eq", "contains", "gt", "gte", "lt", "lte"
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fact_condition(fact: str) -> Dict[str, Any]:
    return {"kind": "fact", "fact": fact, "operator": "eq", "value": True}


def _screen(
    screen_id: str,
    screen_kind: str,
    title_key: str,
    body_key: str,
    completion_fact: str,
    actions: Sequence[str],
    next_screen_id: Optional[str] = None,
) -> Dict[str, Any]:
    transitions: list[Dict[str, Any]] = []
    if next_screen_id:
        transitions.append({
            "next_screen_id": next_screen_id,
            "reason_code": f"{completion_fact}_observed",
            "priority": 0,
            "condition": _fact_condition(completion_fact),
        })
    return {
        "screen_id": screen_id,
        "screen_kind": screen_kind,
        "title_key": title_key,
        "body_key": body_key,
        "required": screen_kind == "optimizer_ranked_result",
        "completion_evidence": _fact_condition(completion_fact),
        "actions": list(actions),
        "transitions": transitions,
        "presentation": {"surface": "cli"},
    }


def _shipped_bundle() -> Dict[str, Any]:
    definition = {
        "schema_version": SCHEMA_VERSION,
        "product_id": PRODUCT_ID,
        "journey_id": JOURNEY_ID,
        "journey_version": JOURNEY_VERSION,
        "entry_screen_id": "search-space",
        "first_success_fact": FIRST_SUCCESS_FACT,
        "published_at": "2026-08-04T00:00:00Z",
        "source_revision": _digest("wisent-optimizer:first-use:2026-08-04.1"),
        "screens": [
            _screen(
                "search-space", "optimizer_search_space", "search_space.title", "search_space.body",
                "search_space_acknowledged", ["continue", "not_now"], "objective",
            ),
            _screen(
                "objective", "optimizer_objective", "objective.title", "objective.body",
                "objective_acknowledged", ["continue", "not_now"], "guardrails",
            ),
            _screen(
                "guardrails", "optimizer_guardrails", "guardrails.title", "guardrails.body",
                "guardrails_acknowledged", ["continue", "not_now"], "ranked-result",
            ),
            _screen(
                "ranked-result", "optimizer_ranked_result", "ranked_result.title", "ranked_result.body",
                FIRST_SUCCESS_FACT, ["run_optimization", "not_now"],
            ),
        ],
        "analytics_contract": {
            "contract_version": "1",
            "surface": "cli.first-use",
            "exposure_event": "onboarding_step_viewed",
            "primary_action_event": "onboarding_first_action_completed",
            "completion_event": "onboarding_completed",
            "first_success_event": "onboarding_first_success_observed",
        },
        "experiment_contract": {
            "experiment_id": "first-use-cli-2026-08-04",
            "control_variant_id": "control",
            "eligible_variant_ids": ["control"],
            "assignment_unit": "device",
            "reward_event": "onboarding_completed",
            "guardrail_events": ["optimization_failed"],
            "owner": "wisent-optimizer",
            "kill_switch": False,
        },
    }
    canonical_definition = _canonical(definition)
    return {
        "journey_version_id": _SHIPPED_VERSION_ID,
        "definition": definition,
        "canonical_definition": canonical_definition,
        "content_sha256": _digest(canonical_definition),
        "source_revision": definition["source_revision"],
    }


CANONICAL_BUNDLE = _shipped_bundle()

