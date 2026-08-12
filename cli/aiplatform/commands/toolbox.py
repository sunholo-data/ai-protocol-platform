"""`aiplatform toolbox` — operator tools for the MCP Toolbox database gateway.

Two subcommands, both operator-loop (no agent code path):

  * ``validate`` — run Toolbox's own boot-time config validation over a
    ``tools.yaml`` (sources, tool params, dataset existence) AND apply our two
    extra static gates: no ``templateParameters`` (the bypassable-substring
    injection vector) and ``writeMode: blocked`` on every source. Catches a
    broken config on the laptop instead of at container start.
  * ``probe`` — a minimal MCP client: ``tools/list`` then optionally
    ``tools/call`` against ANY MCP server over Streamable HTTP. Not
    Toolbox-specific; it is the reproduction path used throughout the Toolbox
    spike. Sends the ``Accept: application/json, text/event-stream`` header the
    protocol requires (a plain ``application/json`` Accept gets a 406 from some
    MCP servers — a trap worth wrapping).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx

# The example ships in the template; the real one is client config. Prefer the
# real one when present (local dev), fall back to the example.
# parents: [0]=commands [1]=aiplatform [2]=cli [3]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLBOX_DIR = _REPO_ROOT / "infrastructure" / "mcp-toolbox"


def _default_config() -> Path | None:
    for name in ("tools.yaml", "tools.example.yaml"):
        p = _TOOLBOX_DIR / name
        if p.exists():
            return p
    return None


def mcp_rpc(url: str, method: str, params: dict | None = None) -> dict:
    """One MCP JSON-RPC call over Streamable HTTP.

    Module-level (not nested in ``probe``) so ``aiplatform bq`` reuses it rather
    than growing a second, subtly different MCP client.

    The ``Accept`` header carries BOTH media types because the protocol requires
    it — a plain ``application/json`` Accept gets a 406 from some MCP servers,
    which is a confusing failure to debug from scratch.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"cannot reach {url}: {exc}") from exc
    if resp.status_code != 200:
        raise click.ClickException(f"{method} → HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


@click.group()
def toolbox() -> None:
    """MCP Toolbox database-gateway operator tools."""


@toolbox.command("validate")
@click.argument("config", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=False)
@click.pass_context
def validate(ctx: click.Context, config: Path | None) -> None:
    """Validate a Toolbox tools.yaml (defaults to infrastructure/mcp-toolbox/).

    Runs the same static gates CI enforces, then — if the ``toolbox`` binary is
    on PATH or at ``.bin/toolbox`` — boots it in a throwaway parse to surface any
    error Toolbox itself would raise (missing param description, unknown source
    type, non-existent dataset).
    """
    path = config or _default_config()
    if path is None:
        raise click.ClickException(f"no tools.yaml found under {_TOOLBOX_DIR} — pass one explicitly")

    click.echo(f"validating {path}")

    # --- Static gates (mirror backend/tests/tool_tests/test_toolbox_config_safety.py) ---
    # Parse the YAML structure rather than grepping raw text — the words
    # "templateParameters" and "writeMode" legitimately appear in comments/docs,
    # and only their use in an actual tool/source mapping is a finding.
    import yaml  # local import: keeps `toolbox probe` usable without pyyaml

    docs = [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    tools = [d for d in docs if d.get("kind") == "tool"]
    sources = [d for d in docs if d.get("kind") == "source"]

    problems: list[str] = []
    offenders = [t.get("name") for t in tools if "templateParameters" in t]
    if offenders:
        problems.append(
            f"tools {offenders} use `templateParameters` — a bypassable SQL-injection "
            "vector (C2/C2b). Use CASE on a bound param (columns) or UNION ALL + literal "
            "label (tables)."
        )
    unblocked = [s.get("name") for s in sources if s.get("writeMode") != "blocked"]
    if unblocked:
        problems.append(
            f"sources {unblocked} are not `writeMode: blocked` — Toolbox defaults to "
            "`allowed` (arbitrary DML); every source must set blocked."
        )
    if problems:
        for p in problems:
            click.echo(click.style(f"  ✗ {p}", fg="red"))
        raise click.ClickException("static validation failed")
    click.echo(click.style("  ✓ static gates: no templateParameters, all sources read-only", fg="green"))

    # --- Optional: Toolbox's own validation ---
    # Toolbox has no dry-validate flag: a bad config makes it exit non-zero at
    # boot, a good one prints "Initialized … tools" then SERVES (blocks). So we
    # launch it, poll the output until it either dies or reports readiness, then
    # kill it — never blocking on the running server.
    binary = shutil.which("toolbox") or str(_REPO_ROOT / ".bin" / "toolbox")
    if not Path(binary).exists():
        click.echo("  · toolbox binary not found (run `make toolbox-install`) — skipped deep validation")
        return

    # Bind an ephemeral loopback port; we kill it before it serves a request.
    proc = subprocess.Popen(  # noqa: S603 — fixed args, operator tool
        [binary, "--config", str(path), "--address", "127.0.0.1", "--port", "5099", "--disable-reload"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    captured: list[str] = []
    ok = False
    try:
        deadline = time.monotonic() + 15
        assert proc.stdout is not None
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break  # process exited (config error)
                continue
            captured.append(line.rstrip())
            if "Server ready to serve" in line or "Initialized" in line and "tools:" in line:
                ok = True
                break
            if "ERROR" in line.upper():
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if ok:
        click.echo(click.style("  ✓ toolbox parsed the config and initialized all tools", fg="green"))
    else:
        click.echo(click.style("  ✗ toolbox rejected the config:", fg="red"))
        for line in captured[-6:]:
            click.echo(f"      {line}")
        raise click.ClickException("toolbox validation failed")


@toolbox.command("probe")
@click.argument("url")
@click.option("--call", "tool_name", default=None, help="After listing, call this tool.")
@click.option("--args", "args_json", default="{}", help="JSON arguments for --call.")
@click.pass_context
def probe(ctx: click.Context, url: str, tool_name: str | None, args_json: str) -> None:
    """Probe any MCP server: tools/list, then optionally tools/call.

    URL is the toolset-scoped endpoint, e.g.
    http://127.0.0.1:5000/mcp/example
    """

    def _rpc(method: str, params: dict | None = None) -> dict:
        return mcp_rpc(url, method, params)

    listing = _rpc("tools/list")
    tools = listing.get("result", {}).get("tools", [])
    click.echo(f"{len(tools)} tool(s) at {url}:")
    for t in tools:
        required = t.get("inputSchema", {}).get("required", [])
        click.echo(f"  • {t['name']}  (required: {required or 'none'})")

    if not tool_name:
        return

    try:
        arguments = json.loads(args_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"--args is not valid JSON: {exc}") from exc

    click.echo(f"\ncalling {tool_name}({arguments})…")
    result = _rpc("tools/call", {"name": tool_name, "arguments": arguments})
    content = result.get("result", {}).get("content", [])
    if result.get("result", {}).get("isError") or "error" in result:
        click.echo(click.style("  tool returned an error:", fg="red"))
    click.echo(f"  {len(content)} content item(s):")
    for item in content[:10]:
        click.echo(f"    {item.get('text', item)[:200]}")
    if len(content) > 10:
        click.echo(f"    … +{len(content) - 10} more")


if __name__ == "__main__":  # pragma: no cover
    toolbox()
    sys.exit(0)
