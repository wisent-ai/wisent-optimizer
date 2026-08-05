"""Durable first-use journey for the Wisent steering optimizer CLI."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
import re
import socket
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

PRODUCT_ID = "wisent-optimizer"
JOURNEY_ID = "first-use"
JOURNEY_VERSION = "2026-08-04.1"
SCHEMA_VERSION = 1
FIRST_SUCCESS_FACT = "ranked_configuration_observed"
CLIENT_ID = "wisent-optimizer"
TOKEN_ENV = "WISENT_OPTIMIZER_STADO_INTEGRATION_TOKEN"
BASE_URL_ENV = "STADO_INTEGRATION_API_URL"
_FALLBACK_VERSION_ID = "7d4ad04d-8301-4b1e-9e38-d7eb27bf693f"
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


def _fallback_bundle() -> Dict[str, Any]:
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
        "journey_version_id": _FALLBACK_VERSION_ID,
        "definition": definition,
        "canonical_definition": canonical_definition,
        "content_sha256": _digest(canonical_definition),
        "source_revision": definition["source_revision"],
    }


CANONICAL_FALLBACK = _fallback_bundle()


def _validate_condition(condition: Any, depth: int = 0) -> None:
    if depth > 16 or not isinstance(condition, dict):
        raise ValueError("journey condition is invalid")
    kind = condition.get("kind")
    if kind in {"all", "any"}:
        children = condition.get("conditions")
        if not isinstance(children, list) or len(children) > 32:
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
    if not isinstance(screens, list) or not screens or len(screens) > 128:
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
        if not isinstance(transitions, list) or len(transitions) > 128:
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


class StadoTransport:
    """Small synchronous adapter for the canonical Stado onboarding operations."""

    def __init__(self, base_url: str, token: str, timeout: float = 3.0):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Stado base URL must be an HTTPS origin")
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("Stado base URL must be an HTTPS origin")
        if not token.strip():
            raise ValueError("Stado integration token is required")
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._token = token
        self._timeout = timeout

    @classmethod
    def from_environment(cls) -> Optional["StadoTransport"]:
        base_url = os.environ.get(BASE_URL_ENV, "").strip()
        token = os.environ.get(TOKEN_ENV, "").strip()
        if not base_url or not token:
            return None
        try:
            return cls(base_url, token)
        except ValueError:
            return None

    def _post(self, operation: str, body: Mapping[str, Any]) -> Any:
        endpoint = f"{self._origin}/integration/{CLIENT_ID}/onboarding/{PRODUCT_ID}/{operation}"
        request = Request(
            endpoint,
            data=_canonical(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = response.read(_MAX_BUNDLE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            raise RuntimeError("Stado onboarding transport failed") from error
        if len(payload) > _MAX_BUNDLE_BYTES:
            raise RuntimeError("Stado onboarding response is too large")
        try:
            envelope = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Stado onboarding response is invalid") from error
        if not isinstance(envelope, dict) or envelope.get("ok") is not True or "result" not in envelope:
            raise RuntimeError("Stado onboarding operation failed")
        return envelope["result"]

    def read_bundle(self) -> Dict[str, Any]:
        return self._post("bundle.read", {
            "product_id": PRODUCT_ID,
            "journey_id": JOURNEY_ID,
            "journey_version": JOURNEY_VERSION,
            "if_none_match": None,
        })

    def assign_experiment(self, subject_hash: str) -> Any:
        return self._post("experiments.assign", {
            "product_id": PRODUCT_ID,
            "app_id": PRODUCT_ID,
            "platform": "cli",
            "surface": "cli.first-use",
            "subject": subject_hash,
        })

    def collect_event(self, event: Mapping[str, Any]) -> None:
        self._post("events.collect", event)

    def read_state(self, attempt_id: str, subject_hash: str) -> Any:
        return self._post("state.read", {
            "product_id": PRODUCT_ID,
            "attempt_id": attempt_id,
            "subject_hash": subject_hash,
        })


class JourneyStorage:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or self.default_path()

    @staticmethod
    def default_path() -> Path:
        root = os.environ.get("XDG_STATE_HOME")
        base = Path(root).expanduser() if root else Path.home() / ".local" / "state"
        return base / PRODUCT_ID / "onboarding.json"

    def load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"schema_version": 1, "bundles": {}, "progress": {}, "events": []}
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return {"schema_version": 1, "bundles": {}, "progress": {}, "events": []}
        if not isinstance(value.get("bundles"), dict):
            value["bundles"] = {}
        if not isinstance(value.get("progress"), dict):
            value["progress"] = {}
        if not isinstance(value.get("events"), list):
            value["events"] = []
        return value

    def save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".onboarding-", dir=str(self.path.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _valid_progress(progress: Any, bundle: Mapping[str, Any], subject_hash: str) -> bool:
    if not isinstance(progress, dict):
        return False
    screen_ids = {screen["screen_id"] for screen in bundle["definition"]["screens"]}
    completed = progress.get("completed_screen_ids")
    return (
        _UUID.fullmatch(str(progress.get("attempt_id", ""))) is not None
        and progress.get("product_id") == PRODUCT_ID
        and progress.get("journey_version_id") == bundle["journey_version_id"]
        and progress.get("subject_hash") == subject_hash
        and progress.get("scope_kind") == "device"
        and progress.get("current_screen_id") in screen_ids
        and isinstance(completed, list)
        and all(screen_id in screen_ids for screen_id in completed)
        and progress.get("status") in {"in_progress", "skipped", "completed", "abandoned"}
        and _SHA256.fullmatch(str(progress.get("evidence_revision", ""))) is not None
        and isinstance(progress.get("answers"), list)
        and isinstance(progress.get("evidence"), dict)
    )


class JourneyRuntime:
    def __init__(self, subject_hash: str, storage: Optional[JourneyStorage] = None):
        if not _SHA256.fullmatch(subject_hash):
            raise ValueError("subject hash is invalid")
        self.subject_hash = subject_hash
        self.storage = storage or JourneyStorage()
        self.transport = StadoTransport.from_environment()
        self.state: Dict[str, Any] = {}
        self.bundle: Dict[str, Any] = {}
        self.progress: Dict[str, Any] = {}

    def start(self, reset: bool = False) -> None:
        self.state = self.storage.load()
        self.flush_events()
        bundle: Optional[Dict[str, Any]] = None
        if self.transport:
            try:
                bundle = validate_bundle(self.transport.read_bundle())
                self.state["bundles"][JOURNEY_ID] = bundle
            except (RuntimeError, ValueError):
                bundle = None
        if bundle is None:
            cached = self.state["bundles"].get(JOURNEY_ID)
            try:
                bundle = validate_bundle(cached)
            except (ValueError, TypeError):
                bundle = validate_bundle(CANONICAL_FALLBACK)
        self.bundle = bundle
        progress = self.state["progress"].get(self.subject_hash)
        resumable = _valid_progress(progress, bundle, self.subject_hash)
        new_attempt = reset or not resumable
        if new_attempt:
            progress = {
                "attempt_id": str(uuid.uuid4()),
                "product_id": PRODUCT_ID,
                "journey_version_id": bundle["journey_version_id"],
                "subject_hash": self.subject_hash,
                "scope_kind": "device",
                "current_screen_id": bundle["definition"]["entry_screen_id"],
                "completed_screen_ids": [],
                "status": "in_progress",
                "evidence_revision": _digest("{}"),
                "answers": [],
                "evidence": {},
                "first_action_recorded": False,
            }
        self.progress = progress
        self.state["progress"][self.subject_hash] = progress
        self.storage.save(self.state)
        if self.transport:
            try:
                self.transport.read_state(progress["attempt_id"], self.subject_hash)
            except RuntimeError:
                pass
            if not progress.get("experiment_id"):
                try:
                    assignment = self.transport.assign_experiment(self.subject_hash)
                    if isinstance(assignment, dict):
                        experiment_id = assignment.get("experimentId") or assignment.get("experiment_id")
                        variant_id = assignment.get("variant") or assignment.get("variant_id")
                        if isinstance(experiment_id, str) and isinstance(variant_id, str):
                            progress["experiment_id"] = experiment_id
                            progress["variant_id"] = variant_id
                            self.storage.save(self.state)
                except RuntimeError:
                    pass
        if new_attempt:
            self.emit("onboarding_started")
        elif progress["status"] == "in_progress":
            self.emit("onboarding_resumed")

    @property
    def screen(self) -> Dict[str, Any]:
        screen_id = self.progress.get("current_screen_id")
        for screen in self.bundle["definition"]["screens"]:
            if screen["screen_id"] == screen_id:
                return screen
        raise ValueError("current journey screen is missing")

    def emit(
        self,
        event_name: str,
        properties: Optional[Mapping[str, Any]] = None,
        screen_id: Optional[str] = None,
        decision: Optional[Mapping[str, str]] = None,
    ) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "event_name": event_name,
            "attempt_id": self.progress["attempt_id"],
            "product_id": PRODUCT_ID,
            "journey_version_id": self.progress["journey_version_id"],
            "subject_hash": self.subject_hash,
            "scope_kind": self.progress["scope_kind"],
            "screen_id": screen_id or self.progress["current_screen_id"],
            "occurred_at": _utc_now(),
            "evidence_revision": self.progress["evidence_revision"],
            "properties": dict(properties or {}),
            "answers": list(self.progress.get("answers", [])),
        }
        if self.progress.get("experiment_id"):
            event["experiment_id"] = self.progress["experiment_id"]
        if self.progress.get("variant_id"):
            event["variant_id"] = self.progress["variant_id"]
        if decision:
            event.update(decision)
        self.state["events"].append(event)
        self.storage.save(self.state)
        if self.transport:
            try:
                self.transport.collect_event(event)
            except RuntimeError:
                return
            self.state["events"] = [queued for queued in self.state["events"] if queued.get("event_id") != event["event_id"]]
            self.storage.save(self.state)

    def flush_events(self) -> None:
        if not self.transport:
            return
        pending = list(self.state.get("events", []))
        for event in pending:
            try:
                self.transport.collect_event(event)
            except RuntimeError:
                return
            self.state["events"] = [queued for queued in self.state["events"] if queued.get("event_id") != event.get("event_id")]
            self.storage.save(self.state)

    def expose(self) -> None:
        self.emit("onboarding_step_viewed")

    def acknowledge(self) -> bool:
        if self.progress.get("status") != "in_progress":
            return False
        screen = self.screen
        facts = {
            "optimizer_search_space": "search_space_acknowledged",
            "optimizer_objective": "objective_acknowledged",
            "optimizer_guardrails": "guardrails_acknowledged",
        }
        fact = facts.get(screen["screen_kind"])
        if not fact:
            return False
        if not self.progress.get("first_action_recorded"):
            self.progress["first_action_recorded"] = True
            self.storage.save(self.state)
            self.emit("onboarding_first_action_completed")
        self.progress.setdefault("evidence", {})[fact] = True
        self._update_revision()
        decision = _select_next(self.bundle["definition"], screen["screen_id"], self.progress["evidence"])
        if decision is None:
            return False
        completed = set(self.progress.get("completed_screen_ids", []))
        completed.add(screen["screen_id"])
        self.progress["completed_screen_ids"] = sorted(completed)
        self.progress["current_screen_id"] = decision["selected_next_screen_id"]
        self.storage.save(self.state)
        self.emit("onboarding_step_completed", screen_id=screen["screen_id"], decision=decision)
        self.complete_if_ready()
        return True

    def skip(self) -> None:
        if self.progress.get("status") == "completed":
            return
        self.progress["status"] = "skipped"
        self.storage.save(self.state)
        self.emit("onboarding_step_skipped")

    def observe_ranked_configuration(self, properties: Mapping[str, Any]) -> None:
        evidence = self.progress.setdefault("evidence", {})
        evidence[FIRST_SUCCESS_FACT] = True
        evidence["ranked_configuration"] = dict(properties)
        self._update_revision()
        self.storage.save(self.state)
        self.complete_if_ready(properties)

    def complete_if_ready(self, properties: Optional[Mapping[str, Any]] = None) -> bool:
        if self.progress.get("status") == "completed" or self.screen.get("transitions"):
            return self.progress.get("status") == "completed"
        condition = self.screen.get("completion_evidence")
        if condition and not _evaluate(condition, self.progress.get("evidence", {})):
            return False
        completed_screen = self.screen["screen_id"]
        completed = set(self.progress.get("completed_screen_ids", []))
        completed.add(completed_screen)
        self.progress["completed_screen_ids"] = sorted(completed)
        self.progress["status"] = "completed"
        self.storage.save(self.state)
        event_properties = dict(properties or self.progress.get("evidence", {}).get("ranked_configuration", {}))
        self.emit("onboarding_step_completed", event_properties, completed_screen)
        self.emit("onboarding_first_success_observed", event_properties, completed_screen)
        self.emit("onboarding_completed", event_properties, completed_screen)
        return True

    def _update_revision(self) -> None:
        self.progress["evidence_revision"] = _digest(_canonical(self.progress.get("evidence", {})))


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


def _default_subject_hash(subject: Optional[str] = None) -> str:
    supplied = subject or os.environ.get("WISENT_OPTIMIZER_ONBOARDING_SUBJECT")
    stable_subject = supplied if supplied else f"{getpass.getuser()}@{socket.gethostname()}:{Path.home()}"
    return _digest(stable_subject)


def _ranking_observation(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, Mapping) or result.get("error"):
        return None
    method_ranking = result.get("method_ranking")
    if isinstance(method_ranking, Mapping):
        scores = {
            str(name): float(score)
            for name, score in method_ranking.items()
            if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(float(score))
        }
        tested = result.get("total_configurations_tested")
        if scores and isinstance(tested, int) and tested > 0:
            best_name, best_score = max(scores.items(), key=lambda item: item[1])
            return {
                "ranking_kind": "method_ranking",
                "configuration_count": tested,
                "ranked_method_count": len(scores),
                "best_method": best_name,
                "best_score": best_score,
            }
    grid = result.get("grid_search_results")
    if isinstance(grid, list):
        scored = [
            item for item in grid
            if isinstance(item, Mapping)
            and isinstance(item.get("score"), (int, float))
            and not isinstance(item.get("score"), bool)
            and math.isfinite(float(item["score"]))
            and ("layer" in item or "strength" in item or "method" in item)
        ]
        if scored:
            best = max(scored, key=lambda item: float(item["score"]))
            observation: Dict[str, Any] = {
                "ranking_kind": "grid_search_results",
                "configuration_count": len(scored),
                "best_score": float(best["score"]),
            }
            for key in ("method", "layer", "strength"):
                if isinstance(best.get(key), (str, int, float)) and not isinstance(best.get(key), bool):
                    observation[f"best_{key}"] = best[key]
            return observation
    return None


def record_ranked_configuration(result: Any, model_name: str, task_name: str) -> bool:
    """Record first success only when a real optimizer result contains a ranking."""
    observation = _ranking_observation(result)
    if observation is None:
        return False
    observation.update({"model_name": model_name, "task_name": task_name})
    try:
        runtime = JourneyRuntime(_default_subject_hash())
        runtime.start()
        runtime.observe_ranked_configuration(observation)
    except (OSError, RuntimeError, ValueError):
        # Onboarding telemetry and persistence must never block the optimization result.
        return False
    return True


def _print_screen(runtime: JourneyRuntime) -> None:
    title, body = _RENDERERS[runtime.screen["screen_kind"]]
    print(f"\nWisent Optimizer first use — {title}\n")
    print(body)
    if runtime.progress.get("status") == "completed":
        print("\nStatus: complete — a real ranked configuration was observed.")
    elif runtime.screen["screen_kind"] == "optimizer_ranked_result":
        print("\nStatus: waiting for ranked_configuration_observed from the optimizer result path.")


def _run_onboarding(args: argparse.Namespace) -> int:
    runtime = JourneyRuntime(_default_subject_hash(args.subject))
    runtime.start(reset=args.reset)
    if args.not_now:
        runtime.skip()
        print("Onboarding saved. Run `wisent-optimizer onboarding` to resume.")
        return 0
    if runtime.progress.get("status") == "skipped":
        runtime.progress["status"] = "in_progress"
        runtime.storage.save(runtime.state)
        runtime.emit("onboarding_resumed")
    runtime.complete_if_ready()
    _print_screen(runtime)
    runtime.expose()
    if runtime.progress.get("status") == "completed" or args.status:
        return 0
    if args.advance:
        if runtime.acknowledge():
            _print_screen(runtime)
        else:
            print("\nThis step cannot be completed by a click; run a real optimization and inspect its ranking.")
        return 0
    if not sys.stdin.isatty():
        print("\nAdvance one explanation step with `wisent-optimizer onboarding --advance`.")
        return 0
    while runtime.screen["screen_kind"] != "optimizer_ranked_result":
        answer = input("\nPress Enter to continue, or type q to save and exit: ").strip().lower()
        if answer in {"q", "quit", "exit"}:
            runtime.skip()
            print("Progress saved.")
            return 0
        runtime.acknowledge()
        _print_screen(runtime)
        runtime.expose()
    print("\nExit onboarding now and run the optimizer; completion is recorded from its ranked-result return path.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wisent-optimizer",
        description="Wisent steering optimization utilities, including the durable first-use journey.",
    )
    commands = parser.add_subparsers(dest="command")
    onboarding = commands.add_parser(
        "onboarding",
        help="learn the search space, objective, and guardrails, then produce a real ranked configuration",
    )
    onboarding.add_argument("--subject", help="stable local subject used only after SHA-256 hashing")
    onboarding.add_argument("--advance", action="store_true", help="acknowledge one explanation step")
    onboarding.add_argument("--status", action="store_true", help="show the current durable journey step")
    onboarding.add_argument("--reset", action="store_true", help="start a new first-use attempt")
    onboarding.add_argument("--not-now", action="store_true", help="save progress and pause the journey")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "onboarding":
        return _run_onboarding(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
