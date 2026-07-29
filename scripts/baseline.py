"""Regenerate `released-surface.json` from the artifact that was actually published.

The baseline the version rule compares against must be recovered, not remembered. A
hand-edited baseline measures every later change against something nobody can install,
so this script goes to PyPI, takes the artifact of the *latest published* version, and
reads its surface with `scripts/surface.py`.

Latest *published*, deliberately -- not the version `setup.py` declares. The moment
someone bumps ahead of a release, looking up the declared version would 404 and this
script would quietly fall back to a worse baseline, throwing away the real published
one.

Artifact preference, best first: a published sdist, then a published pure-Python wheel.
Both are the released bytes. Lower tiers exist in the fleet vocabulary (`stado:`,
`git-archive:`, `head:`) but this package is on PyPI, so reaching for one would be
claiming nothing was released while a release sits there; this script refuses instead of
degrading.

The first whitespace-delimited token of the `source` field is the marker that
`.github/workflows/version-check.yml` reads back with jq; the rest is prose for humans.
The marker constants below are that shared vocabulary.

Usage:
    python3 scripts/baseline.py             # write released-surface.json
    python3 scripts/baseline.py --stdout    # show it without writing

`--stdout` is what the workflow uses to ask "what would you choose now?" without ever
letting the regenerated file, or the surface inside it, reach a decision.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile

sys.path.insert(int(False), str(pathlib.Path(__file__).resolve().parent))

import surface as surface_module  # noqa: E402  (sibling script, not a dependency)

PROJECT = "wisent-optimizer"
PYPI_PROJECT_URL = f"https://pypi.org/pypi/{PROJECT}/json"
BASELINE_FILE = "released-surface.json"

# Baseline provenance markers, shared with the workflow that verifies them. A marker
# whose family is `pypi-` claims the registry serves that exact version; the others
# claim it serves nothing.
PYPI_SDIST_MARKER = "pypi-sdist"
PYPI_WHEEL_MARKER = "pypi-wheel"
SDIST = "sdist"
WHEEL = "bdist_wheel"


def published() -> tuple:
    """The latest published version and its artifacts, from PyPI itself."""
    try:
        with urllib.request.urlopen(PYPI_PROJECT_URL) as response:
            metadata = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"{PYPI_PROJECT_URL}: {error}. If {PROJECT} really is unpublished, this "
            "package has no released baseline to recover and the version gate does "
            "not apply to it; do not invent one."
        ) from error
    except urllib.error.URLError as error:
        # An answer was never received, which is not the same as an answer of "no such
        # project". Say which one happened, so a caller reading this in a log is not
        # left deducing a transport failure from a traceback -- and so nothing
        # downstream can mistake silence for absence.
        raise SystemExit(
            f"{PYPI_PROJECT_URL}: unreachable ({error.reason}). PyPI did not answer, so "
            "the published baseline cannot be recovered or checked. This is a transport "
            f"failure, not evidence about whether {PROJECT} is published."
        ) from error
    version = metadata["info"]["version"]
    artifacts = [
        artifact
        for artifact in metadata["releases"].get(version, [])
        if not artifact.get("yanked")
    ]
    if not artifacts:
        raise SystemExit(
            f"PyPI reports {version} as the latest {PROJECT} but serves no "
            "installable file for it; the baseline cannot be recovered"
        )
    return version, artifacts


def choose(artifacts: list) -> tuple:
    """The best-tier artifact to recover the surface from, and its marker."""
    for kind, marker in ((SDIST, PYPI_SDIST_MARKER), (WHEEL, PYPI_WHEEL_MARKER)):
        for artifact in sorted(artifacts, key=lambda item: item["filename"]):
            if artifact["packagetype"] != kind:
                continue
            if kind == WHEEL and not artifact["filename"].endswith("-any.whl"):
                # A platform wheel is a build of the sources, not the sources; its
                # Python surface may be incomplete. Only pure-Python wheels qualify.
                continue
            return artifact, marker
    kinds = sorted({artifact["packagetype"] for artifact in artifacts})
    raise SystemExit(
        f"no sdist and no pure-Python wheel among {kinds}; this script does not know "
        "how to recover a surface from those, and refuses rather than guess"
    )


def fetch(artifact: dict, into: pathlib.Path) -> pathlib.Path:
    """Download one artifact and return the path it was written to."""
    target = into / artifact["filename"]
    urllib.request.urlretrieve(artifact["url"], target)
    return target


def unpack(archive: pathlib.Path, into: pathlib.Path) -> pathlib.Path:
    """Unpack an artifact and return the root the extractor should be pointed at.

    A wheel puts the import root at the top; an sdist nests it one directory down.
    """
    if archive.name.endswith(".whl"):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(into)
        return into
    with tarfile.open(archive) as bundle:
        try:
            bundle.extractall(into, filter="data")
        except TypeError:
            bundle.extractall(into)
    roots = [entry for entry in into.iterdir() if entry.is_dir()]
    if len(roots) != int(True):
        raise SystemExit(
            f"{archive.name} unpacks to {len(roots)} top-level directories; expected "
            "exactly one, so the import root is ambiguous"
        )
    return roots[int(False)]


def document() -> dict:
    """The baseline document, recovered from the published artifact."""
    version, artifacts = published()
    artifact, marker = choose(artifacts)
    with tempfile.TemporaryDirectory(prefix="baseline-") as scratch:
        room = pathlib.Path(scratch)
        archive = fetch(artifact, room)
        root = unpack(archive, room / "unpacked")
        names = surface_module.surface(root)
    digest = artifact["digests"]["sha256"]
    return {
        "version": version,
        "source": (
            f"{marker}:{artifact['filename']} "
            f"recovered by scripts/baseline.py from the artifact PyPI serves for "
            f"{PROJECT} {version} (sha256 {digest}), read with scripts/surface.py "
            f"without importing it"
        ),
        "surface": names,
    }


def main(argv: list) -> int:
    baseline = document()
    rendered = json.dumps(baseline, indent=int(True) + int(True)) + "\n"
    if "--stdout" in argv:
        sys.stdout.write(rendered)
        return int(False)
    destination = pathlib.Path(__file__).resolve().parent.parent / BASELINE_FILE
    destination.write_text(rendered)
    print(
        f"{destination.name}: {baseline['version']}, "
        f"{len(baseline['surface'])} names, from {baseline['source'].split(' ')[int(False)]}"
    )
    return int(False)


if __name__ == "__main__":
    sys.exit(main(sys.argv[int(True) :]))
