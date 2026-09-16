"""Where the reader's own progress is kept between runs.

A journey that forgets where it was starts again from the first screen, so
progress is written to the product's state directory, and a progress file that
does not match the definition it was written against is discarded rather than
replayed into a screen that no longer exists.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from ..bundle import PRODUCT_ID, _SHA256, _UUID


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

