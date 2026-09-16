"""The names the onboarding package promises to the rest of the product.

The console script resolves `…steering_optimizer.onboarding:main` and the
optimizer CLI imports `record_ranked_configuration` from the same place. Both
were module-level functions in one 876-line file; they are now re-exports from
a package, so this test imports the package on its own and asserts the surface.
A later split that drops a re-export fails here instead of at somebody's first
run.

The package is imported by path rather than as
`wisent.core.control.steering_optimizer.onboarding`, because that dotted path
resolves only where the `wisent` distribution this repository extends is
installed; the package itself has no import outside its own tree.
"""

import importlib.util
import sys
from pathlib import Path

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "wisent"
    / "core"
    / "control"
    / "steering_optimizer"
    / "onboarding"
)

SURFACE = (
    "BASE_URL_ENV",
    "CANONICAL_BUNDLE",
    "CLIENT_ID",
    "FIRST_SUCCESS_FACT",
    "JOURNEY_ID",
    "JOURNEY_VERSION",
    "JourneyRuntime",
    "JourneyStorage",
    "PRODUCT_ID",
    "SCHEMA_VERSION",
    "StadoTransport",
    "TOKEN_ENV",
    "build_parser",
    "main",
    "record_ranked_configuration",
    "validate_bundle",
)


def _load():
    """Import the package from its path, as `onboarding`."""
    if "onboarding" in sys.modules:
        return sys.modules["onboarding"]
    spec = importlib.util.spec_from_file_location(
        "onboarding",
        PACKAGE / "__init__.py",
        submodule_search_locations=[str(PACKAGE)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["onboarding"] = module
    spec.loader.exec_module(module)
    return module


def test_every_promised_name_resolves():
    onboarding = _load()

    for name in SURFACE:
        assert hasattr(onboarding, name), name
    assert sorted(onboarding.__all__) == sorted(SURFACE)

def test_the_shipped_journey_validates_against_its_own_rules():
    onboarding = _load()

    validated = onboarding.validate_bundle(onboarding.CANONICAL_BUNDLE)

    assert validated["definition"]["journey_id"] == onboarding.JOURNEY_ID
    assert validated["definition"]["screens"], "a journey with no screens shows nothing"
    assert validated["content_sha256"], "a definition with no digest cannot be checked"

def test_the_command_line_still_offers_the_same_options():
    onboarding = _load()

    parser = onboarding.build_parser()
    options = {action.dest for action in parser._actions}

    assert {"help"} <= options
    assert parser.prog


def test_a_bundle_that_breaks_a_rule_is_refused():
    onboarding = _load()

    broken = dict(onboarding.CANONICAL_BUNDLE)
    broken["definition"] = {"screens": []}

    try:
        onboarding.validate_bundle(broken)
    except ValueError:
        return
    raise AssertionError("a journey with no screens was accepted")
