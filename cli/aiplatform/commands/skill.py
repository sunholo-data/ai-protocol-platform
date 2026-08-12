"""`aiplatform skill` — skill-side dev affordances.

Today: a single `probe` command that fires one streaming chat turn at the
backend with `?probe=1` so the LATENCY_REPORT AG-UI Custom event rides at
the end of the stream. Prints the per-stage breakdown (request_received,
session_index_done, before_agent_done, before_model_done, first_model_token,
first_agui_event, first_sse_byte) plus model + routing + tools count.

Used to:
  * Sanity-check chat latency from a terminal without opening a browser.
  * Run the M5 A/B baseline (AITANA_TTFT_MODE=full vs off).
  * Diagnose where time is being spent in production traffic without
    scraping logs.

See docs/design/v6.1.0/ttft-instrumentation.md.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json as _json
import sys as _sys
import uuid
from pathlib import Path

import click
import httpx
import yaml

from aiplatform.http import AIPlatformClient, APIError, resolve_base_url

# Repo root, so `skill push` can find backend/skills/templates/<name>/SKILL.md
# from a working tree. cli/aiplatform/commands/skill.py -> parents[3] = repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATES_DIR = _REPO_ROOT / "backend" / "skills" / "templates"

# Stage names emitted by backend/observability/timing.py — keep in sync.
_STAGES_IN_ORDER = (
    "request_received",
    "session_index_done",
    "before_agent_done",
    "before_model_done",
    "first_model_token",
    "first_agui_event",
    "first_sse_byte",
)


@click.group()
def skill() -> None:
    """Skill-side dev affordances (probe, etc.)."""


@skill.command("list")
@click.option("--jobs", "jobs_only", is_flag=True, help="Show only job skills (metadata.job=true).")
@click.pass_context
def list_skills(ctx: click.Context, jobs_only: bool) -> None:
    """List skills the caller can access.

    With --jobs, shows only the delegatable "job" skills (v6.8.0 8.3) — the SAME
    access-scoped set a door discovers via `delegation.discover_jobs`, so this is
    how you check what an opted-in door would offer a given user.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    try:
        skills = client.get("/api/skills")
    except APIError as exc:
        raise click.ClickException(str(exc)) from exc

    rows: list[tuple[str, str, str, str]] = []
    for s in skills:
        md = s.get("skillMetadata") or {}
        is_job = bool(md.get("job"))
        if jobs_only and not is_job:
            continue
        badge = f"job:{md.get('jobFloor', 'confirm')}" if is_job else ""
        rows.append((s.get("slug") or s.get("skillId", ""), s.get("displayName", ""), str(md.get("model", "")), badge))

    if not rows:
        click.echo("No job skills found." if jobs_only else "No skills found.")
        return
    for slug, name, model, badge in rows:
        tail = click.style(f"  [{badge}]", fg="cyan") if badge else ""
        click.echo(f"{slug:34} {name:30} {model}{tail}")


@skill.command("probe")
@click.argument("skill_id")
@click.option(
    "--message",
    "-m",
    default="Hello",
    show_default=True,
    help="Test message to send to the skill.",
)
@click.option(
    "--session",
    default=None,
    help="Existing session/thread id to resume. Default: a fresh thread.",
)
@click.option(
    "--timeout",
    default=60.0,
    show_default=True,
    type=float,
    help="HTTP timeout for the streaming request, in seconds.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Print the raw LATENCY_REPORT payload as JSON instead of the table.",
)
@click.pass_context
def probe(
    ctx: click.Context,
    skill_id: str,
    message: str,
    session: str | None,
    timeout: float,
    json_output: bool,
) -> None:
    """Fire one chat turn at SKILL_ID and print the TTFT breakdown.

    Sends POST /api/skill/{SKILL_ID}/stream?probe=1 with a minimal
    AG-UI HttpAgent body, reads the SSE stream, finds the LATENCY_REPORT
    Custom event at end-of-stream, and pretty-prints it.

    Requires AITANA_TTFT_MODE != "off" on the backend; off mode is a
    true no-op and emits no LATENCY_REPORT.
    """
    env = ctx.obj["env"]
    base_url = resolve_base_url(env)
    client = AIPlatformClient(env=env, base_url=base_url)
    headers = client._auth_headers()  # noqa: SLF001  internal helper, intentional
    headers["Accept"] = "text/event-stream"

    thread_id = session or f"probe-{uuid.uuid4().hex[:12]}"
    body = {
        "threadId": thread_id,
        "runId": f"run-probe-{uuid.uuid4().hex[:8]}",
        "messages": [
            {"id": f"msg-{uuid.uuid4().hex[:8]}", "role": "user", "content": message},
        ],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    url = f"{base_url}/api/skill/{skill_id}/stream"
    report: dict | None = None
    error_event: dict | None = None
    delegations: list[dict] = []
    reliability_events: list[dict] = []
    event_count = 0

    try:
        with httpx.stream(
            "POST",
            url,
            headers=headers,
            params={"probe": "1"},
            json=body,
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 400:
                # Drain the response so the error message reaches the user.
                detail = resp.read().decode("utf-8", errors="replace")
                raise APIError(f"POST /api/skill/{skill_id}/stream returned {resp.status_code}: {detail}")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                try:
                    event = _json.loads(payload)
                except ValueError:
                    continue
                event_count += 1
                name = event.get("name")
                if name == "LATENCY_REPORT":
                    value = event.get("value")
                    if isinstance(value, dict):
                        report = value
                elif name == "AGENT_DELEGATION":
                    value = event.get("value")
                    if isinstance(value, dict):
                        delegations.append(value)
                elif name in ("MODEL_RETRY", "MODEL_FALLBACK"):
                    # MODEL-RELIABILITY M4: reliability events in the probe
                    # trace — headless verification of retry/fallback paths.
                    value = event.get("value")
                    if isinstance(value, dict):
                        reliability_events.append({"name": name, **value})
                elif event.get("type") == "RUN_ERROR":
                    error_event = event
    except httpx.HTTPError as exc:
        raise APIError(f"HTTP transport error during probe: {exc}") from exc

    if delegations and not json_output:
        # Surface handoffs even on error — the delegation fires before a
        # downstream failure (e.g. a delegate's tool being blocked).
        click.secho("Delegations (AGENT_DELEGATION):", bold=True, err=True)
        for d in delegations:
            mode = d.get("mode", "?")
            verb = "proposed" if mode == "suggest" else "→"
            click.secho(
                f"  {verb} {d.get('target_display') or d.get('target')}  ({mode}, from {d.get('parent')})",
                err=True,
            )

    if reliability_events and not json_output:
        click.secho("Model reliability (MODEL_RETRY / MODEL_FALLBACK):", bold=True, err=True)
        for ev in reliability_events:
            if ev["name"] == "MODEL_RETRY":
                click.secho(
                    f"  retry #{ev.get('attempt')} on {ev.get('model')} ({ev.get('code')}, {ev.get('delay_s')}s)",
                    err=True,
                )
            else:
                reason = f", {ev['reason']}" if ev.get("reason") else ""
                click.secho(
                    f"  fallback {ev.get('from_model')} -> {ev.get('to_model')} ({ev.get('code')}{reason})",
                    fg="yellow",
                    err=True,
                )

    if error_event is not None:
        click.secho(f"RUN_ERROR: {error_event.get('message', '(no message)')}", fg="red", err=True)
        ctx.exit(1)

    if report is None:
        click.secho(
            "No LATENCY_REPORT in stream. "
            "Backend may have AITANA_TTFT_MODE=off, or the stream ended before "
            f"the report event was emitted ({event_count} non-data events seen).",
            fg="yellow",
            err=True,
        )
        ctx.exit(2)

    if json_output:
        click.echo(
            _json.dumps({**report, "delegations": delegations} if delegations else report, indent=2, sort_keys=True)
        )
        return

    _print_table(report)


SEVERITY_CHOICES = ("material", "moderate", "cosmetic")


def _parse_identity(value: str) -> dict[str, str]:
    """Map a --left/--right value to the doc_id | gs_url duality the compare
    tools accept. A `gs://` prefix is a bucket object; anything else is a
    doc_id (bare or `doc:...` — the backend normalises)."""
    v = value.strip()
    if v.startswith("gs://"):
        return {"gs_url": v}
    return {"doc_id": v}


def _build_compare_config(clauses: str | None, severity: str | None, max_other: int | None) -> dict:
    """Assemble the start_compare `config`.

    Mirrors CompareConfigForm's omit-at-default contract so a full-scope run
    reuses the legacy (non-variant) cache keys: keys are present ONLY when the
    caller narrowed that dimension. An all-default invocation yields `{}`.
    """
    config: dict = {}
    if clauses:
        parsed = [c.strip() for c in clauses.split(",") if c.strip()]
        if parsed:
            config["clauses"] = parsed
    if severity:
        config["severity_floor"] = severity
    if max_other is not None:
        config["max_other_clauses"] = max_other
    return config


@skill.command("compare")
@click.argument("skill_id")
@click.option("--left", "left", required=True, help="Left contract: a doc_id or a gs://bucket/object URL.")
@click.option("--right", "right", required=True, help="Right contract: a doc_id or a gs://bucket/object URL.")
@click.option(
    "--clauses",
    default=None,
    help="Comma-separated clause subset (e.g. settlement_type,price_formula). Omit for all 12 standard clauses.",
)
@click.option(
    "--severity",
    default=None,
    type=click.Choice(SEVERITY_CHOICES),
    help="Severity floor for the narrative diff (material|moderate|cosmetic). Omit to show all severities.",
)
@click.option(
    "--max-other",
    "max_other",
    default=None,
    type=int,
    help="Cap on non-standard (other) clauses. Omit for the backend default (20).",
)
@click.option(
    "--session",
    default=None,
    help="Existing session/thread id to reuse. Default: bootstrap a fresh `compare-*` session.",
)
@click.option(
    "--timeout",
    default=120.0,
    show_default=True,
    type=float,
    help="HTTP timeout for the streaming request, in seconds.",
)
@click.option(
    "--pretty",
    is_flag=True,
    default=False,
    help="Pretty-print each AG-UI event with indent=2 (default: compact one-line-per-event).",
)
@click.pass_context
def compare(
    ctx: click.Context,
    skill_id: str,
    left: str,
    right: str,
    clauses: str | None,
    severity: str | None,
    max_other: int | None,
    session: str | None,
    timeout: float,
    pretty: bool,
) -> None:
    """Start a scoped PPA comparison headlessly and stream the AG-UI result.

    Drives the SAME `start_compare` action the workbench CompareLauncher fires:
    POSTs to ``/api/skills/{SKILL_ID}/sessions/{SESSION_ID}/surface-action-run``
    with ``action.context = {left, right, config}`` and consumes the
    ``text/event-stream`` response — one compact JSON per line (grep-friendly)
    or --pretty. Lets us smoke the whole launcher path without a browser.

    --left/--right accept a doc_id or a gs://bucket/object URL (mixed allowed).
    --clauses / --severity / --max-other narrow the run pre-diff; omit them all
    for a full comparison (which reuses the legacy extraction/diff caches).

    With no --session, a fresh `compare-<uuid>` session is bootstrapped first
    (POST /api/sessions/{sid}/bootstrap) so the surface-action-run gate finds a
    live session to write the action into.

    Exit codes:
      0  Stream terminated with `RUN_FINISHED`.
      1  Stream terminated with `RUN_ERROR` (or ended with no terminal event).
      2  HTTP error from the endpoint (e.g. 403 — skill not opted in via
         `allow_action_triggered_runs: true`). Response body printed to stderr.

    Requires the skill to opt in via
    ``tool_configs.a2ui.allow_action_triggered_runs: true``.
    """
    env = ctx.obj["env"]
    base_url = resolve_base_url(env)
    client = AIPlatformClient(env=env, base_url=base_url)

    session_id = session or f"compare-{uuid.uuid4().hex[:12]}"
    if session is None:
        # Bootstrap a fresh session so the action gate finds it (idempotent).
        try:
            client.post(f"/api/sessions/{session_id}/bootstrap", json={"skill_id": skill_id})
        except APIError as exc:
            click.echo(f"Failed to bootstrap session {session_id}: {exc}", err=True)
            ctx.exit(2)
            return

    config = _build_compare_config(clauses, severity, max_other)
    body: dict[str, object] = {
        "surfaceId": "workspace",
        "action": {
            "name": "start_compare",
            "sourceComponentId": None,
            "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
            "context": {
                "left": _parse_identity(left),
                "right": _parse_identity(right),
                "config": config,
            },
        },
        "forwardedProps": {"a2ui_surface_state": {}},
    }

    headers = client._auth_headers()  # noqa: SLF001  internal helper, intentional
    headers["Accept"] = "text/event-stream"
    url = f"{base_url}/api/skills/{skill_id}/sessions/{session_id}/surface-action-run"

    click.secho(f"compare {skill_id}  ({session_id})  scope={config or 'full'}", bold=True, err=True)

    terminal_type: str | None = None
    try:
        with httpx.stream("POST", url, headers=headers, json=body, timeout=timeout) as resp:
            if resp.status_code >= 400:
                detail = resp.read().decode("utf-8", errors="replace")
                click.echo(f"HTTP {resp.status_code} from POST {url}\n{detail}", err=True)
                ctx.exit(2)
                return
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if not payload:
                    continue
                try:
                    event = _json.loads(payload)
                except ValueError:
                    click.echo(f"Skipping malformed SSE payload: {payload}", err=True)
                    continue
                if pretty:
                    click.echo(_json.dumps(event, indent=2, sort_keys=True))
                else:
                    click.echo(_json.dumps(event, separators=(",", ":"), sort_keys=True))
                _sys.stdout.flush()
                event_type = event.get("type")
                if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                    terminal_type = event_type
    except httpx.HTTPError as exc:
        raise APIError(f"HTTP transport error during compare: {exc}") from exc

    if terminal_type == "RUN_ERROR":
        ctx.exit(1)
    elif terminal_type == "RUN_FINISHED":
        ctx.exit(0)
    else:
        click.echo("Stream ended without a terminal RUN_FINISHED or RUN_ERROR event.", err=True)
        ctx.exit(1)


@skill.command("set")
@click.argument("skill_id")
@click.option(
    "--model-tier",
    "model_tier",
    required=True,
    help=(
        "Model to assign to the skill. A logical tier (lite|smart|...) or a raw "
        "registry model id. Not validated against a fixed list — the backend/"
        "registry resolves it."
    ),
)
@click.pass_context
def set_skill(ctx: click.Context, skill_id: str, model_tier: str) -> None:
    """Set SKILL_ID's model on its skillMetadata.

    GETs the skill, merges `model` into skillMetadata (preserving every other
    skillMetadata field), PUTs it back, and prints the resulting model.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    current = client.get(f"/api/skills/{skill_id}")
    metadata = dict(current.get("skillMetadata") or {})
    metadata["model"] = model_tier
    updated = client.put(f"/api/skills/{skill_id}", json={"skillMetadata": metadata})
    result_meta = updated.get("skillMetadata") or {}
    click.echo(f"skillMetadata.model = {result_meta.get('model', model_tier)}")


def _parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter dict, instructions body).

    Mirrors backend/admin/platform_seed._parse_template so a CLI push produces
    the same fields the deploy-time seeder would.
    """
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text.strip()
    frontmatter = yaml.safe_load(parts[1]) or {}
    return frontmatter, parts[2].strip()


def _template_payload(frontmatter: dict, instructions: str) -> dict:
    """Build the /api/skills PUT body from a parsed template.

    Uses API alias keys (skillMetadata / displayName / accessControl /
    initialMessage) that UpdateSkillRequest accepts. `instructions` and the
    `metadata` block are the source of truth; other keys are pushed when present.
    """
    payload: dict = {"instructions": instructions}
    for src, dst in (
        ("description", "description"),
        ("metadata", "skillMetadata"),
        ("display_name", "displayName"),
        ("access_control", "accessControl"),
        ("initial_message", "initialMessage"),
        ("tags", "tags"),
        ("welcome", "welcome"),
    ):
        value = frontmatter.get(src)
        if value:
            payload[dst] = value
    return payload


def _resolve_template_path(current: dict, template_name: str | None, file_path: Path | None) -> Path:
    """Locate the SKILL.md: --file wins, else templates/<name>/SKILL.md."""
    if file_path is not None:
        return Path(file_path)
    name = template_name or current.get("name")
    if not name:
        raise click.UsageError("Could not determine the template name from the live skill; pass --template or --file.")
    path = _TEMPLATES_DIR / name / "SKILL.md"
    if not path.exists():
        raise click.UsageError(f"Template not found: {path}. Pass --file to point at the SKILL.md explicitly.")
    return path


def _render_diff(current: dict, payload: dict, instructions: str, file_path: Path) -> tuple[bool, list[str]]:
    """Print the instructions diff + changed top-level fields.

    Returns (instructions_changed, changed_field_names). Shared by push/diff.
    """
    old_instr = (current.get("instructions") or "").splitlines()
    new_instr = instructions.splitlines()
    diff = list(difflib.unified_diff(old_instr, new_instr, fromfile="live", tofile=str(file_path), lineterm=""))
    changed_fields = [k for k in payload if k != "instructions" and payload[k] != current.get(k)]
    instr_changed = old_instr != new_instr

    click.echo(f"  instructions: {'changed' if instr_changed else 'unchanged'}")
    click.echo(f"  fields to update: {', '.join(changed_fields) or '(none)'}")
    if diff:
        click.echo()
        for line in diff[:60]:
            color = "green" if line.startswith("+") else "red" if line.startswith("-") else None
            click.secho(line, fg=color)
        if len(diff) > 60:
            click.echo(f"… (+{len(diff) - 60} more diff lines)")
    return instr_changed, changed_fields


# Shared options for the template-vs-live commands (push / diff).
def _template_options(func):
    func = click.option(
        "--file",
        "file_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="SKILL.md to use. Defaults to backend/skills/templates/<skill-name>/SKILL.md.",
    )(func)
    func = click.option(
        "--template",
        "template_name",
        default=None,
        help="Template dir name under backend/skills/templates/ (if it differs from the live skill's name).",
    )(func)
    return func


@skill.command("push")
@click.argument("skill_id")
@_template_options
@click.option("--dry-run", is_flag=True, help="Show the diff without writing.")
@click.pass_context
def push_skill(
    ctx: click.Context,
    skill_id: str,
    file_path: Path | None,
    template_name: str | None,
    dry_run: bool,
) -> None:
    """Push a local SKILL.md template to SKILL_ID's live config.

    Refreshes instructions + skillMetadata (+ description / displayName /
    accessControl / initialMessage / tags / welcome when present) from the
    on-disk template — the same fields the deploy-time platform seeder pushes,
    but for a single skill and via the authed API. Use it to try a SKILL.md
    edit against a running env without redeploying or hand-writing a Firestore
    update. `--dry-run` shows the instructions diff first.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    current = client.get(f"/api/skills/{skill_id}")
    path = _resolve_template_path(current, template_name, file_path)
    frontmatter, instructions = _parse_skill_md(path.read_text())
    payload = _template_payload(frontmatter, instructions)

    click.secho(f"push {skill_id}  ←  {path}", bold=True)
    instr_changed, changed_fields = _render_diff(current, payload, instructions, path)

    if dry_run:
        click.echo()
        click.secho("dry-run — nothing written.", fg="yellow")
        return
    if not instr_changed and not changed_fields:
        click.echo()
        click.secho("already up to date — nothing to push.", fg="yellow")
        return

    updated = client.put(f"/api/skills/{skill_id}", json=payload)
    click.echo()
    click.secho(f"✓ pushed to {updated.get('skillId', skill_id)} ({updated.get('name', '')}).", fg="green")


@skill.command("diff")
@click.argument("skill_id")
@_template_options
@click.pass_context
def diff_skill(ctx: click.Context, skill_id: str, file_path: Path | None, template_name: str | None) -> None:
    """Show SKILL_ID's live config vs the local SKILL.md template (read-only).

    Same comparison `push --dry-run` prints, as a first-class verb — check for
    drift between disk and a running env before you push or redeploy.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    current = client.get(f"/api/skills/{skill_id}")
    path = _resolve_template_path(current, template_name, file_path)
    frontmatter, instructions = _parse_skill_md(path.read_text())
    payload = _template_payload(frontmatter, instructions)

    click.secho(f"diff {skill_id}  (live ↔ {path})", bold=True)
    instr_changed, changed_fields = _render_diff(current, payload, instructions, path)
    if not instr_changed and not changed_fields:
        click.echo()
        click.secho("in sync — live matches the template.", fg="green")


@skill.command("pull")
@click.argument("skill_id")
@click.option("--field", default=None, help="Print only this top-level field (e.g. instructions, skillMetadata).")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the JSON to a file instead of stdout.",
)
@click.pass_context
def pull_skill(ctx: click.Context, skill_id: str, field: str | None, out_path: Path | None) -> None:
    """Fetch SKILL_ID's live config (read-only).

    Prints the full skill JSON, a single --field (e.g. `--field instructions`
    dumps the live body), or writes the JSON to --out for inspection/backup.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    current = client.get(f"/api/skills/{skill_id}")

    if field:
        if field not in current:
            raise click.UsageError(f"Field {field!r} not present. Available: {', '.join(sorted(current))}")
        value = current[field]
        click.echo(value if isinstance(value, str) else _json.dumps(value, indent=2))
        return

    text = _json.dumps(current, indent=2)
    if out_path:
        Path(out_path).write_text(text + "\n")
        click.secho(f"✓ wrote {out_path} ({len(text)} bytes).", fg="green")
    else:
        click.echo(text)


@skill.command("seed")
@click.pass_context
def seed_skills(ctx: click.Context) -> None:
    """Run the platform seed hook (POST /api/admin/seed-platform-skills).

    Refreshes ALL platform skills from their on-disk SKILL.md templates — the
    same operation Cloud Build runs on deploy. Requires an ADMIN token in
    AIPLATFORM_ID_TOKEN (the allowlisted Cloud Build SA, or a Firebase token
    with the `aitana-admin` group tag). To refresh a SINGLE skill without admin
    rights, use `skill push` instead.
    """
    client = AIPlatformClient(env=ctx.obj["env"])
    summary = client.post("/api/admin/seed-platform-skills", json={})
    click.secho("✓ seed complete.", fg="green")
    click.echo(_json.dumps(summary, indent=2))


def _print_table(report: dict) -> None:
    """Pretty-print the LATENCY_REPORT payload as a 2-col table."""
    click.echo()
    click.secho("TTFT breakdown", bold=True)
    click.echo("─" * 40)
    for stage in _STAGES_IN_ORDER:
        key = f"{stage}_ms"
        value = report.get(key)
        formatted = f"{value:>8.2f}ms" if isinstance(value, (int, float)) else "       —"
        marker = "  ← TTFT" if stage == "first_model_token" else ""
        click.echo(f"  {stage:<22}{formatted}{marker}")

    click.echo("─" * 40)
    total = report.get("total_response_ms")
    if isinstance(total, (int, float)):
        click.echo(f"  {'total':<22}{total:>8.2f}ms")
    click.echo()
    click.echo(
        f"  model:   {report.get('model_used') or '—'}    "
        f"routing: {report.get('routing_choice') or '—'}    "
        f"tools:   {report.get('tools_invoked_count', 0)}"
    )
    click.echo(f"  mode:    {report.get('ttft_mode') or '—'}")
    click.echo()
