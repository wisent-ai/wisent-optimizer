"""Print this package's public surface: what `wisent-optimizer` promises callers.

This package ships no console script, no HTTP route and no benchmark registry. It is
a library that a caller reaches in exactly two ways, and both are the contract:

1. **Re-exported symbols.** Every `__init__.py` under the shipped package declares
   `__all__`; those names, qualified by the module a caller imports them from, are the
   import surface. `SteeringOptimizer` is promised from two modules (the package root
   and `.optimizer`), and dropping either import breaks callers who spelled it that
   way, so the module is part of the name rather than flattened away.

2. **The `optimization_type` dispatch strings.** `run_steering_optimization` selects
   behaviour by comparing its `optimization_type` argument against string literals.
   These are registry identifiers in everything but name: a caller passes
   `"comprehensive"` and gets a pipeline. Worse than raising, an unrecognised value
   returns `{"error": ...}`, so renaming one fails silently at the caller. They are
   therefore listed -- but only the literals the code actually compares against, never
   the ones a docstring merely advertises.

Private helpers (`_summary_to_dict`, `_parse_layer_range`) and the mixin classes behind
`SteeringOptimizer` are deliberately absent: they are how the package works, not what
it promised.

Read with `ast`, never by importing. Importing pulls in `optuna` and `hyperopt`, and a
release decision must not depend on a machine having them; the modules also import
`wisent.core.primitives` and `wisent.core.control.steering_methods` from sibling
distributions that need not be installed at all. Reading statically also means this
runs unchanged against an unpacked wheel, so the surface of an already published
version is recovered exactly rather than assumed.

Usage:
    python3 scripts/surface.py [root]     # root defaults to the repository
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

PACKAGE = ("wisent", "core", "control", "steering_optimizer")
DISPATCH_PARAMETER = "optimization_type"
EXPORT_PREFIX = "export"
DISPATCH_PREFIX = DISPATCH_PARAMETER


def parse(source: pathlib.Path) -> ast.Module:
    """Parse one module, refusing rather than skipping it."""
    try:
        return ast.parse(source.read_text(), filename=str(source))
    except OSError as error:
        raise SystemExit(f"{source}: {error}") from error
    except SyntaxError as error:
        # Refuse rather than skip. A module that does not parse cannot be imported
        # either, so its names are unreachable at runtime; skipping it would report a
        # smaller surface, and the rule would read that as a removed promise. The
        # surface is unknown here, not shrunk.
        raise SystemExit(
            f"{source}: does not parse, so the surface is unknown: {error}"
        ) from error


def string_elements(node: ast.AST, source: pathlib.Path) -> list:
    """The string literals of an `__all__` sequence, or refuse."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        raise SystemExit(
            f"{source}: __all__ is not a literal sequence, so the export surface "
            "cannot be read statically; refusing rather than reporting fewer exports"
        )
    names = []
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            raise SystemExit(
                f"{source}: __all__ holds a non-literal entry, so the export surface "
                "is unknown; refusing rather than reporting fewer exports"
            )
        names.append(element.value)
    return names


def exported_names(tree: ast.Module, source: pathlib.Path) -> list:
    """The `__all__` entries of one package initialiser."""
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(
            node.target, ast.Name
        ):
            targets, value = [node.target], node.value
        else:
            continue
        if not any(target.id == "__all__" for target in targets):
            continue
        found = (found or []) + string_elements(value, source)
    if found is None:
        raise SystemExit(
            f"{source}: a package initialiser with no __all__. What this package "
            "re-exports would then be a matter of opinion and the surface unknown; "
            "declare __all__ rather than let the contract be inferred"
        )
    return found


def dispatch_names(tree: ast.Module) -> list:
    """String literals the code compares `optimization_type` against.

    Only comparison against the dispatch parameter counts. A literal that appears in a
    docstring is advertised, not accepted, and must not be reported as promised.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == DISPATCH_PARAMETER):
            continue
        for operator, comparator in zip(node.ops, node.comparators):
            if not isinstance(operator, (ast.Eq, ast.In)):
                continue
            candidates = (
                comparator.elts
                if isinstance(comparator, (ast.List, ast.Tuple, ast.Set))
                else [comparator]
            )
            for candidate in candidates:
                if isinstance(candidate, ast.Constant) and isinstance(
                    candidate.value, str
                ):
                    found.append(candidate.value)
    return found


def module_path(source: pathlib.Path, root: pathlib.Path) -> str:
    """The dotted module a caller imports, from the file's location."""
    parts = list(source.relative_to(root).with_suffix("").parts)
    if parts[-int(True)] == "__init__":
        parts.pop()
    return ".".join(parts)


def surface(root: pathlib.Path) -> list:
    """Every name this package promises callers, sorted and unique."""
    package = root.joinpath(*PACKAGE)
    if not package.is_dir():
        raise SystemExit(
            f"{package} is not a directory; is {root} the root of the repository or "
            "of an unpacked wheel?"
        )
    names = set()
    for source in sorted(package.rglob("*.py")):
        tree = parse(source)
        if source.name == "__init__.py":
            dotted = module_path(source, root)
            for name in exported_names(tree, source):
                names.add(f"{EXPORT_PREFIX}:{dotted}:{name}")
        for name in dispatch_names(tree):
            names.add(f"{DISPATCH_PREFIX}:{name}")
    if not names:
        raise SystemExit(
            f"no promised names found under {package}. Either the package moved, or "
            "it stopped declaring __all__ -- both change what it promises, so "
            "refusing rather than reporting an empty surface"
        )
    return sorted(names)


def main(argv: list) -> int:
    root = (
        pathlib.Path(argv[int(False)])
        if argv
        else pathlib.Path(__file__).resolve().parent.parent
    )
    print(json.dumps({"surface": surface(root)}, indent=int(True) + int(True)))
    return int(False)


if __name__ == "__main__":
    sys.exit(main(sys.argv[int(True) :]))
