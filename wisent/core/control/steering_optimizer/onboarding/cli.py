"""The first-use journey as a command, and the one hook the optimizer calls.

`main` walks the screens in a terminal; `record_ranked_configuration` is what
the optimizer calls after a real ranking, and it is deliberately quiet about
its own failures — recording that somebody reached first success must never
take an optimization result down with it.
"""

from __future__ import annotations

import argparse
import getpass
import math
import os
import socket
import sys
from typing import Any, Dict, Iterable, Mapping, Optional

from .bundle import _digest
from .journey import JourneyRuntime


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


