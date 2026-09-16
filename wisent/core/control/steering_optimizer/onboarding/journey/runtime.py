"""The journey as it actually runs for one reader.

It asks the transport for a definition and uses the one this product ships
with when there is none, validates whatever it got, restores the reader's
progress if it still matches, and moves through the screens as facts are
recorded. Everything it needs is imported; nothing here decides what a screen
says or whether a definition is valid.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Mapping, Optional

from ..bundle import (
    CANONICAL_BUNDLE,
    FIRST_SUCCESS_FACT,
    JOURNEY_ID,
    PRODUCT_ID,
    _SHA256,
    _canonical,
    _digest,
    _utc_now,
)
from ..validation import validate_bundle
from .conditions import _evaluate, _select_next
from .storage import JourneyStorage, _valid_progress
from .transport import StadoTransport


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
                bundle = validate_bundle(CANONICAL_BUNDLE)
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

