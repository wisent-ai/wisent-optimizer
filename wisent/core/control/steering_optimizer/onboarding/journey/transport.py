"""Where a journey definition comes from when the service has one.

One class, one job: ask the Stado integration API for this product's journey
and for nothing else. It carries the token environment variable and the size
limit a definition may not exceed, because a definition large enough to be a
payload is not a definition.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..bundle import (
    BASE_URL_ENV,
    CLIENT_ID,
    JOURNEY_ID,
    JOURNEY_VERSION,
    PRODUCT_ID,
    TOKEN_ENV,
    _MAX_BUNDLE_BYTES,
    _canonical,
)


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

