"""Print the public surface of wisent-optimizer: what it promises callers.

The contract is the names its package initialisers re-export and the
``optimization_type`` strings ``run_steering_optimization`` dispatches on.
Both are read with ``ast``, never by importing, so the answer does not depend
on having optuna, hyperopt or the sibling wisent distributions installed, and
the same reader runs unchanged against an unpacked published artifact.

Usage:
    python3 release/surface.py [root]    # root defaults to the repository

Until 2026-09-06 this was scripts/surface.py; after that directory was
removed the version gate carried a copy of it inside the workflow, and the
README kept naming the deleted file.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PACKAGE = ("wisent", "core", "control", "steering_optimizer")
DISPATCH_PARAMETER = "optimization_type"

def parse(source):
    try:
        return ast.parse(source.read_text(), filename=str(source))
    except (OSError, SyntaxError) as error:
        raise SystemExit(
            f"{source}: surface is unknown because it cannot be parsed: {error}"
        ) from error

def string_elements(node, source):
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        raise SystemExit(
            f"{source}: __all__ is not a literal sequence"
        )
    names = []
    for element in node.elts:
        if not (
            isinstance(element, ast.Constant)
            and isinstance(element.value, str)
        ):
            raise SystemExit(
                f"{source}: __all__ has a non-literal entry"
            )
        names.append(element.value)
    return names

def exported_names(tree, source):
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [
                target
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
            value = node.value
        elif isinstance(
            node, (ast.AnnAssign, ast.AugAssign)
        ) and isinstance(node.target, ast.Name):
            targets, value = [node.target], node.value
        else:
            continue
        if any(target.id == "__all__" for target in targets):
            found = (found or []) + string_elements(value, source)
    if found is None:
        raise SystemExit(
            f"{source}: package initializer has no __all__"
        )
    return found

def dispatch_names(tree):
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (
            isinstance(node.left, ast.Name)
            and node.left.id == DISPATCH_PARAMETER
        ):
            continue
        for operator, comparator in zip(
            node.ops, node.comparators
        ):
            if not isinstance(operator, (ast.Eq, ast.In)):
                continue
            candidates = (
                comparator.elts
                if isinstance(
                    comparator, (ast.List, ast.Tuple, ast.Set)
                )
                else [comparator]
            )
            for candidate in candidates:
                if isinstance(
                    candidate, ast.Constant
                ) and isinstance(candidate.value, str):
                    found.append(candidate.value)
    return found

def public_surface(root):
    package = root.joinpath(*PACKAGE)
    if not package.is_dir():
        raise SystemExit(f"{package} is not a directory")
    names = set()
    for source in sorted(package.rglob("*.py")):
        tree = parse(source)
        if source.name == "__init__.py":
            parts = list(
                source.relative_to(root).with_suffix("").parts
            )
            parts.pop()
            module = ".".join(parts)
            for name in exported_names(tree, source):
                names.add(f"export:{module}:{name}")
        for name in dispatch_names(tree):
            names.add(f"{DISPATCH_PARAMETER}:{name}")
    if not names:
        raise SystemExit(
            f"no promised names found under {package}"
        )
    return sorted(names)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    print(json.dumps({"surface": public_surface(root.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
