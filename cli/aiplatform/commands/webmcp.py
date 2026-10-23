"""Local-only browser feasibility probes; no credentials or deployed journeys."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import click


def _local_url(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise click.BadParameter("Expected an explicit localhost HTTP URL.") from exc
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not port
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise click.BadParameter(
            "Only explicit localhost HTTP URLs with a port and no credentials/query are supported."
        )
    return value


def _find_script() -> Path | None:
    for base in (Path.cwd(), *Path.cwd().parents):
        script = base / "scripts" / "verify-visual-feedback.mjs"
        if script.is_file():
            return script
    return None


def _run(mode: str, url: str | None, output: Path | None = None, experimental_webmcp: bool = False) -> None:
    script = _find_script()
    if script is None:
        raise click.ClickException("Verifier not found; run from the platform checkout.")
    if mode == "ui":
        if url or experimental_webmcp:
            raise click.ClickException("UI mode uses its own synthetic fixture; omit --url and --experimental-webmcp.")
        script = script.with_name("verify-visual-feedback-ui.mjs")
        if not script.is_file():
            raise click.ClickException("UI verifier not found in this checkout.")
        args = ["node", str(script)]
    else:
        args = ["node", str(script), "--mode", mode]
    if experimental_webmcp:
        args += ["--experimental-webmcp"]
    if url:
        args += ["--url", url]
    if output:
        args += ["--output", str(output.resolve())]
    try:
        result = subprocess.run(args, cwd=script.parent.parent, check=False, timeout=90)
    except FileNotFoundError as exc:
        raise click.ClickException("Node.js is required for the local browser verifier.") from exc
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException("Browser verification timed out after 90 seconds.") from exc
    raise click.exceptions.Exit(result.returncode)


@click.group()
def webmcp() -> None:
    """Probe native WebMCP and synthetic SVG capture in an isolated local browser."""


@webmcp.command()
@click.option(
    "--url", required=True, callback=_local_url, help="Explicit local page; no login or existing browser profile."
)
@click.option("--experimental-webmcp", is_flag=True, help="Explicitly enable Chrome WebMCP testing flags.")
def inspect(url: str, experimental_webmcp: bool) -> None:
    """Inspect native API support. Unsupported exits 2; no tools are invoked."""
    _run("inspect", url, experimental_webmcp=experimental_webmcp)


@webmcp.command()
@click.option(
    "--url", callback=_local_url, help="Local page for native API probe (capture uses built-in synthetic fixture)."
)
@click.option("--mode", type=click.Choice(["full", "capture", "ui"]), default="full", show_default=True)
@click.option(
    "--output", type=click.Path(path_type=Path, file_okay=False), help="Optional directory for synthetic PNG evidence."
)
@click.option("--experimental-webmcp", is_flag=True, help="Explicitly enable Chrome WebMCP testing flags.")
def verify(url: str | None, mode: str, output: Path | None, experimental_webmcp: bool) -> None:
    """Verify capture and native registration/invocation; partial support exits 2."""
    _run(mode, url, output, experimental_webmcp)
