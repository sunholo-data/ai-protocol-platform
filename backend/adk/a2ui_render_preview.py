"""Headless preview of a registered result→A2UI mapping (tool-results-as-a2ui / 7.3).

Runs a registered result→A2UI mapping against a typed tool-result JSON file and
prints the A2UI v0.9 messages, schema-validated against the Basic catalog — no
browser, no running backend. This lives in the backend because that's where the
registry lives (``adk.a2ui_result_render`` + the ``adk.a2ui_ppa_render``
mappings); the ``aiplatform a2ui render`` CLI verb is a thin monorepo-dev
wrapper that shells to ``python -m adk.a2ui_render_preview``.

Usage:
    uv run python -m adk.a2ui_render_preview --list
    uv run python -m adk.a2ui_render_preview --mapping ppa_comparison --result out.json
    make a2ui-render MAPPING=ppa_comparison RESULT=out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# Import registers the PPA mappings as a side effect (same as the app's
# composition root). Keeps the preview in sync with what ships.
from adk import a2ui_elicitation_render as _a2ui_elicitation_render  # noqa: F401
from adk import a2ui_obligation_render as _a2ui_obligation_render  # noqa: F401
from adk import a2ui_ppa_render as _a2ui_ppa_render  # noqa: F401
from adk.a2ui_result_render import registered_mapping_names, render_by_name


def validate_a2ui(messages: list[dict]) -> None:
    """Schema-validate A2UI v0.9 messages against the real Basic catalog.

    Raises the validator's error (a subclass of Exception) if invalid.
    """
    from a2ui.basic_catalog import BasicCatalog
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.schema.validator import A2uiValidator

    config = BasicCatalog.get_config("0.9")
    catalog = A2uiSchemaManager(version="0.9", catalogs=[config])._supported_catalogs[0]
    A2uiValidator(catalog).validate(messages)


def render_and_validate(mapping: str, result: Any) -> list[dict]:
    """Run ``mapping`` on ``result`` and schema-validate the output.

    Raises:
        KeyError: unknown mapping name.
        ValueError: the transform declined this result (returned None — e.g. an
            error-shaped tool result).
        Exception: the produced A2UI failed catalog validation.
    """
    messages = render_by_name(mapping, result)  # raises KeyError on unknown name
    if messages is None:
        raise ValueError(f"mapping {mapping!r} declined to render this result (returned None)")
    validate_a2ui(messages)
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="a2ui_render_preview", description="Preview a result→A2UI mapping.")
    parser.add_argument("--mapping", help="Registered mapping name (see --list).")
    parser.add_argument("--result", help="Path to a typed tool-result JSON file.")
    parser.add_argument("--list", action="store_true", help="List registered mapping names and exit.")
    parser.add_argument(
        "--channel",
        choices=["discord"],
        help="Project the A2UI onto a channel's native vocabulary instead of printing raw messages.",
    )
    args = parser.parse_args(argv)

    if args.list:
        for name in registered_mapping_names():
            print(name)
        return 0

    if not args.mapping or not args.result:
        parser.error("--mapping and --result are required (or use --list)")

    try:
        with open(args.result, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"error: could not read --result file: {exc}", file=sys.stderr)
        return 2

    try:
        messages = render_and_validate(args.mapping, result)
    except KeyError:
        known = ", ".join(registered_mapping_names())
        print(f"error: unknown mapping {args.mapping!r}. Known: {known}", file=sys.stderr)
        return 2
    except Exception as exc:  # ValueError (declined) or a validation error
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.channel == "discord":
        return _print_discord_projection(messages)

    print(json.dumps(messages, indent=2))
    return 0


def _print_discord_projection(messages: list[dict]) -> int:
    """Print the Discord embed a channel would render for these messages.

    Lets you see the channel projection of a mapping without a bot token or
    a guild — the same reason `--result` exists for the web surface.
    """
    from channels._a2ui_discord import surface_to_embed, surface_to_text

    payload = {"surfaceId": "preview", "messages": messages}
    embed = surface_to_embed(payload)
    if embed is None:
        print("no embed projection — this surface degrades to text:", file=sys.stderr)
        print(surface_to_text(payload))
        return 0

    print(json.dumps(embed, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
