"""Regenerate released-surface.json: the surface of the version PyPI serves.

The baseline describes the version actually published, never the version the
working tree declares: every version decision is measured against it. The
artifact is the sdist when the release ships one, else the pure-Python wheel,
unpacked and read with the sibling release/surface.py.

Usage:
    python3 release/baseline.py            # rewrite released-surface.json
    python3 release/baseline.py --stdout   # print it instead, change nothing
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = "wisent-optimizer"
BASELINE_FILE = ROOT / "released-surface.json"

sys.path.insert(0, str(HERE))
from surface import public_surface  # noqa: E402


def published_artifact() -> tuple[dict, str, str]:
    """The newest published version and the artifact its surface is read from."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{PROJECT}/json") as response:
        metadata = json.load(response)
    version = metadata["info"]["version"]
    artifacts = [
        artifact
        for artifact in metadata["releases"].get(version, [])
        if not artifact.get("yanked")
    ]
    for kind, marker in (("sdist", "pypi-sdist"), ("bdist_wheel", "pypi-wheel")):
        for artifact in sorted(artifacts, key=lambda item: item["filename"]):
            if artifact["packagetype"] != kind:
                continue
            if kind == "bdist_wheel" and not artifact["filename"].endswith("-any.whl"):
                continue
            return artifact, marker, version
    raise SystemExit(f"{PROJECT} {version} has no recoverable sdist or pure-Python wheel")


def recovered_surface(artifact: dict) -> tuple[list[str], str]:
    """The surface of the artifact, and the sha256 of the bytes it was read from."""
    with urllib.request.urlopen(artifact["url"]) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    with tempfile.TemporaryDirectory() as scratch:
        unpacked = Path(scratch)
        if artifact["filename"].endswith(".whl"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                archive.extractall(unpacked)
            artifact_root = unpacked
        else:
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
                archive.extractall(unpacked, filter="data")
            roots = [entry for entry in unpacked.iterdir() if entry.is_dir()]
            if len(roots) != 1:
                raise SystemExit(f"{artifact['filename']} has {len(roots)} roots")
            artifact_root = roots[0]
        return public_surface(artifact_root), digest


def build() -> dict:
    artifact, marker, version = published_artifact()
    names, digest = recovered_surface(artifact)
    prose = (
        f"recovered by release/baseline.py from the artifact PyPI serves for {PROJECT} {version}"
        f" (sha256 {digest}), read with release/surface.py without importing it"
    )
    return {
        "version": version,
        "source": f"{marker}:{artifact['filename']} {prose}",
        "surface": names,
    }


def main(argv: list[str]) -> int:
    rendered = json.dumps(build(), indent=2) + "\n"
    if "--stdout" in argv:
        sys.stdout.write(rendered)
        return 0
    BASELINE_FILE.write_text(rendered)
    print(f"wrote {BASELINE_FILE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
