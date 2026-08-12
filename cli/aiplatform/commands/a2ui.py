"""`aiplatform a2ui` — A2UI dev affordances.

`render` previews a registered result→A2UI mapping headlessly: it runs the
mapping in the monorepo backend (where the registry lives) against a typed
tool-result JSON and prints the schema-validated A2UI v0.9 messages — no
browser, no running backend. It's a thin dev wrapper around
`python -m adk.a2ui_render_preview`; the render+validate logic lives in the
backend (tool-results-as-a2ui / 7.3, M3).

The `aiplatform` CLI is an isolated tool (it can't import the backend or the
`a2ui` package), so `render` shells to the backend's uv environment. It's a
dev-loop affordance — it needs the monorepo backend present (run from inside the
repo, or set `AIPLATFORM_BACKEND_DIR`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

_PREVIEW_MODULE = "adk.a2ui_render_preview"
# Relative to a candidate `backend/` dir — its presence identifies the backend.
_BACKEND_MARKER = Path("adk") / "a2ui_render_preview.py"


def _find_backend_dir() -> Path | None:
    """Locate the monorepo ``backend/`` dir (dev-only affordance).

    Order: ``AIPLATFORM_BACKEND_DIR`` env, then walk up from cwd looking for
    ``backend/adk/a2ui_render_preview.py``. Returns None if not found.
    """
    env = os.environ.get("AIPLATFORM_BACKEND_DIR")
    if env:
        candidate = Path(env)
        return candidate if (candidate / _BACKEND_MARKER).exists() else None
    for base in [Path.cwd(), *Path.cwd().parents]:
        candidate = base / "backend"
        if (candidate / _BACKEND_MARKER).exists():
            return candidate
    return None


@click.group()
def a2ui() -> None:
    """A2UI dev affordances."""


@a2ui.command("render")
@click.argument("mapping", required=False)
@click.option("--result", "result", type=str, default=None, help="Typed tool-result JSON file.")
@click.option("--list", "list_mappings", is_flag=True, help="List registered mapping names and exit.")
@click.option(
    "--channel",
    type=click.Choice(["discord"]),
    default=None,
    help="Project onto a channel's native vocabulary (Discord embed) instead of raw A2UI.",
)
def render(mapping: str | None, result: str | None, list_mappings: bool, channel: str | None) -> None:
    """Preview a registered result→A2UI mapping headlessly.

    Runs MAPPING against --result and prints the schema-validated A2UI v0.9
    messages. Requires the monorepo backend (dev affordance) — run from inside
    the repo or set AIPLATFORM_BACKEND_DIR.

    With --channel, prints the channel projection instead — what a Discord
    user would actually see — so a surface can be checked without a bot token
    or a guild.

    \b
    Examples:
      aiplatform a2ui render --list
      aiplatform a2ui render ppa_comparison --result comparison.json
      aiplatform a2ui render ppa_comparison --result comparison.json --channel discord
    """
    backend = _find_backend_dir()
    if backend is None:
        raise click.ClickException(
            "backend not found. `a2ui render` runs the mapping in the monorepo "
            "backend — run from inside the repo or set AIPLATFORM_BACKEND_DIR."
        )

    if list_mappings:
        args = ["--list"]
    elif mapping and result:
        args = ["--mapping", mapping, "--result", str(Path(result).resolve())]
        if channel:
            args += ["--channel", channel]
    else:
        raise click.ClickException("MAPPING and --result are required (or use --list).")

    proc = subprocess.run(
        ["uv", "run", "python", "-m", _PREVIEW_MODULE, *args],
        cwd=str(backend),
    )
    sys.exit(proc.returncode)
