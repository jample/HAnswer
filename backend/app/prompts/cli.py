"""CLI tool for prompt inspection (§7.3).

Usage:
    python -m app.prompts.cli list
    python -m app.prompts.cli explain <name>
    python -m app.prompts.cli preview <name> [--kwargs JSON_STRING]

Examples:
    python -m app.prompts.cli list
    python -m app.prompts.cli explain solver
    python -m app.prompts.cli preview parser --kwargs '{"subject_hint":"math"}'
    python -m app.prompts.cli preview solver --kwargs '{"parsed_question":{"subject":"math","grade_band":"junior","topic_path":["代数"],"question_text":"...","given":[],"find":[],"diagram_description":"","difficulty":2,"tags":[],"confidence":0.9}}'
"""

from __future__ import annotations

import argparse
import json
import sys

from app.prompts.registry import PromptRegistry


def _cmd_list(_args: argparse.Namespace) -> int:
    rows = PromptRegistry.list()
    width_name = max(len(r["name"]) for r in rows)
    width_ver = max(len(r["version"]) for r in rows)
    print(f"{'name'.ljust(width_name)}  {'version'.ljust(width_ver)}  decisions  purpose")
    print("-" * (width_name + width_ver + 80))
    for r in rows:
        print(
            f"{r['name'].ljust(width_name)}  "
            f"{r['version'].ljust(width_ver)}  "
            f"{str(r['design_decisions']).rjust(9)}  "
            f"{r['purpose']}"
        )
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    try:
        t = PromptRegistry.get(args.name)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(t.explain())
    return 0


def _cmd_preview(args: argparse.Namespace) -> int:
    try:
        t = PromptRegistry.get(args.name)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2
    kwargs: dict = {}
    if args.kwargs:
        kwargs = json.loads(args.kwargs)
    print(t.preview(**kwargs))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hanswer-prompt",
        description="Inspect HAnswer prompt templates without calling the LLM.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all registered prompts.")

    ex = sub.add_parser("explain", help="Print purpose + design decisions.")
    ex.add_argument("name")

    pv = sub.add_parser("preview", help="Render the full prompt + schema.")
    pv.add_argument("name")
    pv.add_argument(
        "--kwargs",
        help="JSON string of kwargs to pass into the prompt's build() call.",
    )

    args = parser.parse_args(argv)

    if args.cmd == "list":
        return _cmd_list(args)
    if args.cmd == "explain":
        return _cmd_explain(args)
    if args.cmd == "preview":
        return _cmd_preview(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
