"""Command-line entry point (``k8s-sre-agent``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from k8s_sre_agent import __version__
from k8s_sre_agent.scenarios import ScenarioError, load_scenarios


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k8s-sre-agent", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    scenarios = sub.add_parser("scenarios", help="work with failure scenarios")
    actions = scenarios.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("list", "list available scenarios"),
        ("validate", "validate every scenario directory"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("--root", type=Path, default=Path("scenarios"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        scenarios = load_scenarios(args.root)
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.action == "validate":
        print(f"ok: {len(scenarios)} scenarios valid")
        return 0

    width = max((len(s.id) for s in scenarios), default=2)
    for s in scenarios:
        print(f"{s.id:<{width}}  {s.expected.difficulty:<6}  {s.expected.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
