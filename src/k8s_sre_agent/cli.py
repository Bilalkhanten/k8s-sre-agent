"""Command-line entry point (``k8s-sre-agent``)."""

from __future__ import annotations

import argparse
import json
import os
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

    tools = sub.add_parser("tools", help="run a read-only investigation tool by hand")
    tool_actions = tools.add_subparsers(dest="action", required=True)
    call = tool_actions.add_parser("call", help="call one tool and print its output")
    call.add_argument("name", help="tool name, e.g. list_pods")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")
    call.add_argument("--kubeconfig", type=Path, default=Path(".kube/agent.kubeconfig"))
    call.add_argument(
        "--prometheus-url", default=os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
    )
    call.add_argument("--audit-log", type=Path, default=Path("runs/audit.jsonl"))
    return parser


def _call_tool(args: argparse.Namespace) -> int:
    from k8s_sre_agent.tools import build_registry

    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
        registry = build_registry(
            kubeconfig=args.kubeconfig,
            prometheus_url=args.prometheus_url,
            audit_path=args.audit_log,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = registry.call(args.name, arguments)
    print(result.content, file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "tools":
        return _call_tool(args)

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
