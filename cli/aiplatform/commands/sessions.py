"""`aiplatform sessions` — inspect ADK session state for debugging.

Sprint 1.25 — small helper for "is the iframe actually pushing
ui/update-model-context properly?" without staring at backend logs.
Filters session state to the `mcp_app_context.*` namespace by default.

Sprint ACTION-TRIGGER M3.2 — `trigger-action` subcommand wires the
new `/api/skills/{skill_id}/sessions/{session_id}/surface-action-run`
endpoint into the CLI so the Pattern 1 action-triggered agent loop
can be driven (and smoke-tested) from a terminal without Chrome.

Endpoints used:
    GET  /api/sessions/{session_id}                                — session metadata
    GET  /api/sessions/{session_id}/state                          — full ADK state
                                                                     (filtered locally)
    POST /api/skills/{skill_id}/sessions/{session_id}/surface-action-run
                                                                   — write A2UI action
                                                                     + run agent
                                                                     (SSE stream)
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import sys as _sys

import click
import httpx

from aiplatform.http import AIPlatformClient, APIError, resolve_base_url

_NAMESPACE_PREFIX = "mcp_app_context."


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def sessions() -> None:
    """Inspect chat sessions and their iframe-app context."""


@sessions.command("inspect")
@click.argument("session_id")
@click.option(
    "--mcp-context",
    "mcp_context_only",
    is_flag=True,
    help=("Only show the `mcp_app_context.*` namespace (sprint 1.25). Useful for debugging iframe→agent context flow."),
)
@click.pass_context
def inspect(ctx: click.Context, session_id: str, mcp_context_only: bool) -> None:
    """Show metadata + state for SESSION_ID.

    With --mcp-context, prints only the `mcp_app_context.*` namespace
    so you can verify MCP App iframes are pushing
    `ui/update-model-context` correctly.
    """
    client = _client(ctx)
    meta = client.get(f"/api/sessions/{session_id}")
    state = client.get(f"/api/sessions/{session_id}/state") or {}

    if mcp_context_only:
        filtered = {k: v for k, v in state.items() if k.startswith(_NAMESPACE_PREFIX)}
        if not filtered:
            click.echo(
                f"No keys with prefix {_NAMESPACE_PREFIX!r} in session "
                f"{session_id}. Has any MCP App iframe been rendered + "
                f"interacted with in this session?"
            )
            return
        click.echo(_json.dumps(filtered, indent=2, default=str))
        return

    click.echo("=== Session metadata ===")
    click.echo(_json.dumps(meta, indent=2, default=str))
    click.echo("\n=== Session state ===")
    click.echo(_json.dumps(state, indent=2, default=str))


@sessions.command("bootstrap")
@click.argument("session_id")
@click.option("--skill-id", required=True, help="Skill ID to record on the session index.")
@click.pass_context
def bootstrap(ctx: click.Context, session_id: str, skill_id: str) -> None:
    """Pre-create the ChatSessionIndex + ADK session for SESSION_ID.

    Normally called automatically by the frontend on mount. Use this command
    to manually bootstrap a session when debugging iframe context flow or
    testing the session API without going through the chat UI.

    Idempotent: safe to call multiple times for the same SESSION_ID.
    """
    client = _client(ctx)
    result = client.post(
        f"/api/sessions/{session_id}/bootstrap",
        json={"skill_id": skill_id},
    )
    if result is None:
        click.echo("Bootstrap succeeded (session already existed).")
        return
    created = result.get("created", False)
    if created:
        click.echo(f"Session {session_id} bootstrapped (new index created).")
    else:
        click.echo(f"Session {session_id} already existed — no-op.")


@sessions.command("digest")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Emit the raw digest JSON instead of a summary table.")
@click.pass_context
def digest(ctx: click.Context, session_id: str, as_json: bool) -> None:
    """Show the curated Home digest items for SESSION_ID (v6.11.0).

    GETs ``/api/sessions/{SESSION_ID}/activity?view=digest`` — the notable
    (non-``internal``) tool calls plus delegations that feed the workbench Home
    digest. Lets you verify backend notability tagging without opening a browser
    (the Workspace/Home surface is otherwise only visible in the UI).
    """
    client = _client(ctx)
    data = client.get(f"/api/sessions/{session_id}/activity?view=digest") or {}
    if as_json:
        click.echo(_json.dumps(data, indent=2, default=str))
        return

    tool_calls = data.get("tool_calls", []) or []
    delegations = data.get("delegations", []) or []
    if not tool_calls and not delegations:
        click.echo(f"No curated digest items for session {session_id} (no notable tool calls or delegations yet).")
        return
    click.echo(f"=== Digest for session {session_id} ===")
    for d in delegations:
        click.echo(f"  [{d.get('notability', 'notable')}] delegation → {d.get('targetDisplay') or d.get('target')}")
    for t in tool_calls:
        click.echo(f"  [{t.get('notability', 'notable')}] tool: {t.get('name')} ({t.get('status')})")


def _parse_json_option(raw: str | None, flag_name: str) -> object | None:
    """Parse a CLI JSON option, raising a Click usage error on bad JSON."""
    if raw is None:
        return None
    try:
        return _json.loads(raw)
    except ValueError as exc:
        raise click.UsageError(f"--{flag_name} must be valid JSON: {exc}") from exc


@sessions.command("trigger-action")
@click.argument("session_id")
@click.option(
    "--skill",
    "skill_id",
    required=True,
    help="Skill ID whose surface-action-run endpoint should be invoked.",
)
@click.option(
    "--surface",
    "surface_id",
    required=True,
    help="A2UI surface ID the action targets (must already be rendered in the session).",
)
@click.option(
    "--action",
    "action_name",
    required=True,
    help="Action name to dispatch (e.g. `increment`, `submit`).",
)
@click.option(
    "--component",
    "component_id",
    default=None,
    help="Optional `sourceComponentId` — the A2UI component that fired the action.",
)
@click.option(
    "--context",
    "context_json",
    default=None,
    help="Optional `action.context` payload as a JSON string (≤ 4 KB serialised).",
)
@click.option(
    "--state",
    "state_json",
    default=None,
    help=("Optional `forwardedProps.a2ui_surface_state` snapshot as a JSON string. Defaults to `{}` (empty snapshot)."),
)
@click.option(
    "--timeout",
    default=60.0,
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
def trigger_action(
    ctx: click.Context,
    session_id: str,
    skill_id: str,
    surface_id: str,
    action_name: str,
    component_id: str | None,
    context_json: str | None,
    state_json: str | None,
    timeout: float,
    pretty: bool,
) -> None:
    """Trigger an A2UI action on SESSION_ID, run the agent, stream AG-UI events.

    POSTs to ``/api/skills/{SKILL_ID}/sessions/{SESSION_ID}/surface-action-run``
    with the action + optional surface-state snapshot, then consumes the
    ``text/event-stream`` response. Each AG-UI event is printed to stdout —
    one compact JSON per line by default (grep-friendly for the M3.3 smoke
    script) or pretty-printed with --pretty.

    Exit codes:
      0  Stream terminated with `RUN_FINISHED`.
      1  Stream terminated with `RUN_ERROR`.
      2  HTTP error from the endpoint (e.g. 403 — skill not opted in via
         `allow_action_triggered_runs: true`). Response body is printed
         to stderr.

    Requires the skill to opt in via
    ``tool_configs.a2ui.allow_action_triggered_runs: true``; the backend
    returns 403 otherwise (see action-triggered-agent-turn design doc).
    """
    env = ctx.obj["env"]
    base_url = resolve_base_url(env)
    client = AIPlatformClient(env=env, base_url=base_url)
    headers = client._auth_headers()  # noqa: SLF001  internal helper, intentional
    headers["Accept"] = "text/event-stream"

    parsed_context = _parse_json_option(context_json, "context")
    parsed_state = _parse_json_option(state_json, "state")
    if parsed_state is None:
        parsed_state = {}

    body: dict[str, object] = {
        "surfaceId": surface_id,
        "action": {
            "name": action_name,
            "sourceComponentId": component_id,
            "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
            "context": parsed_context,
        },
        "forwardedProps": {"a2ui_surface_state": parsed_state},
    }

    url = f"{base_url}/api/skills/{skill_id}/sessions/{session_id}/surface-action-run"

    terminal_type: str | None = None

    try:
        with httpx.stream(
            "POST",
            url,
            headers=headers,
            json=body,
            timeout=timeout,
        ) as resp:
            if resp.status_code >= 400:
                detail = resp.read().decode("utf-8", errors="replace")
                click.echo(
                    f"HTTP {resp.status_code} from POST {url}\n{detail}",
                    err=True,
                )
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
                    # Malformed event — surface to stderr but keep streaming;
                    # backend should never emit this, so the smoke script
                    # will catch the regression via the stderr capture.
                    click.echo(f"Skipping malformed SSE payload: {payload}", err=True)
                    continue
                if pretty:
                    click.echo(_json.dumps(event, indent=2, sort_keys=True))
                else:
                    # Compact, one-line-per-event — newline-delimited so the
                    # smoke script in M3.3 can grep + line-count.
                    click.echo(_json.dumps(event, separators=(",", ":"), sort_keys=True))
                _sys.stdout.flush()

                event_type = event.get("type")
                if event_type in ("RUN_FINISHED", "RUN_ERROR"):
                    terminal_type = event_type
    except httpx.HTTPError as exc:
        raise APIError(f"HTTP transport error during trigger-action: {exc}") from exc

    if terminal_type == "RUN_ERROR":
        ctx.exit(1)
    elif terminal_type == "RUN_FINISHED":
        ctx.exit(0)
    else:
        # Stream ended without a terminal event — treat as a backend error
        # (per design doc, the dedup wrapper G41 guarantees exactly one
        # terminal event).
        click.echo(
            "Stream ended without a terminal RUN_FINISHED or RUN_ERROR event.",
            err=True,
        )
        ctx.exit(1)


# --- reconcile (v6.23.0 B5/F5/F6 Phase 1) -----------------------------------
#
# "Admin traces not populating correctly for some sessions" — Mark, 2026-08-06
# UAT. The root cause is unknown, and the design doc is explicit that Phase 2
# must not be designed until this measures what is actually missing.
#
# A session's truth lives in three stores that drift independently: ADK's
# canonical events, the Firestore `chat_sessions` metadata mirror, and the trace
# the admin UI renders from those events. Two of the three are backend-only, so
# the comparison runs server-side (GET …/reconcile) and this renders it.
#
# The SWEEP is the actual Phase 1 deliverable — one session tells you nothing;
# the distribution of finding codes across many is what Phase 2 gets designed
# against.

_SEVERITY_MARK = {"error": "✗", "warn": "!", "info": "·"}


def _echo_reconcile(rep: dict) -> None:
    """One session's report, most-severe first."""
    sid = rep.get("session_id", "?")
    click.echo(f"=== {sid} ===")
    skill = rep.get("skill_id") or "(no skill)"
    owner = rep.get("owner_uid") or "(no owner)"
    domain = rep.get("owner_domain") or "(blank)"
    click.echo(f"  skill={skill}  owner={owner}  domain={domain}")

    if not rep.get("mirror_present"):
        click.echo("  mirror:    ABSENT")
    else:
        click.echo(
            f"  mirror:    turnCount={rep.get('mirror_turn_count', 0)}"
            f"  provisional={rep.get('provisional')}  archived={rep.get('archived')}"
        )
    if not rep.get("canonical_present"):
        click.echo("  canonical: ABSENT (transcript unrecoverable)")
    else:
        click.echo(
            f"  canonical: events={rep.get('event_count', 0)}"
            f"  userEvents={rep.get('user_event_count', 0)}"
            f"  toolCalls={rep.get('raw_tool_calls', 0)}"
            f"  responses={rep.get('raw_function_responses', 0)}"
        )
        click.echo(
            f"  trace:     messages={rep.get('trace_messages', 0)}"
            f"  tools={rep.get('trace_tools', 0)}"
            f" (errored={rep.get('trace_tools_errored', 0)})"
            f"  delegations={rep.get('trace_delegations', 0)}"
        )
    order = {"error": 0, "warn": 1, "info": 2}
    for f in sorted(rep.get("findings", []), key=lambda x: order.get(x.get("severity"), 9)):
        mark = _SEVERITY_MARK.get(f.get("severity"), "?")
        click.echo(f"  {mark} {f.get('code')}: {f.get('detail')}")


@sessions.command("reconcile")
@click.argument("session_id", required=False)
@click.option("--all", "sweep", is_flag=True, help="Sweep the most recent sessions instead of one.")
@click.option("--limit", default=20, show_default=True, help="Sweep size (max 100).")
@click.option("--skill-id", default=None, help="Restrict the sweep to one skill.")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a summary.")
@click.option(
    "--mark-lost",
    is_flag=True,
    help="Flag sessions whose transcript is gone so the history LIST says so. Dry-run unless --write.",
)
@click.option("--write", is_flag=True, help="With --mark-lost, actually persist the flag.")
@click.pass_context
def reconcile(
    ctx: click.Context,
    session_id: str | None,
    sweep: bool,
    limit: int,
    skill_id: str | None,
    as_json: bool,
    mark_lost: bool,
    write: bool,
) -> None:
    """Compare the canonical store, the Firestore mirror and the rendered trace.

    Read-only. Reports what is present in one store and absent from another —
    the Phase 1 measurement behind "admin traces not populating correctly".

    \b
    Requires an admin token (the endpoint is `aitana-admin`-gated).
    Examples:
      aiplatform --env dev sessions reconcile <session-id>
      aiplatform --env dev sessions reconcile --all --limit 25
    """
    if mark_lost:
        # A repair pass, not a reconcile — dry by default so an accidental
        # invocation reports rather than writes.
        client = _client(ctx)
        data = (
            client.post(
                f"/api/admin/analytics/sessions-mark-transcript-lost?limit={limit}&dry_run={str(not write).lower()}",
                timeout=max(60.0, limit * 3.0),
            )
            or {}
        )
        if as_json:
            click.echo(_json.dumps(data, indent=2, default=str))
            return
        ids = data.get("marked", []) or []
        verb = "would mark" if not write else "marked"
        click.echo(f"scanned {data.get('scanned', 0)}; {verb} {len(ids)}; already flagged {data.get('already_marked', 0)}")
        for sid in ids:
            click.echo(f"  {sid}")
        if ids and not write:
            click.echo("\nRe-run with --write to persist.")
        return

    if not sweep and not session_id:
        raise click.UsageError("Give a SESSION_ID, or --all to sweep recent sessions.")
    client = _client(ctx)

    if not sweep:
        data = client.get(f"/api/admin/analytics/sessions/{session_id}/reconcile") or {}
        if as_json:
            click.echo(_json.dumps(data, indent=2, default=str))
            return
        _echo_reconcile(data)
        return

    qs = f"/api/admin/analytics/sessions-reconcile-sweep?limit={limit}"
    if skill_id:
        qs += f"&skill_id={skill_id}"
    # A large sweep fans out over N canonical reads server-side; the default 30s
    # transport timeout fails at exactly the size Phase 1 needs.
    data = client.get(qs, timeout=max(60.0, limit * 3.0)) or {}
    if as_json:
        click.echo(_json.dumps(data, indent=2, default=str))
        return

    reports = data.get("reports", []) or []
    counts = data.get("code_counts", {}) or {}
    for rep in reports:
        # Only print sessions that actually diverged — a clean sweep of 25 should
        # not scroll 25 healthy reports past the operator.
        if [f for f in rep.get("findings", []) if f.get("severity") in ("error", "warn")]:
            _echo_reconcile(rep)
            click.echo("")

    scanned = data.get("scanned", len(reports))
    click.echo(f"=== swept {scanned} session(s) ===")
    if not counts:
        click.echo("  no findings")
        return
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        pct = (100 * n / scanned) if scanned else 0
        click.echo(f"  {code:24s} {n:4d}  ({pct:.0f}%)")
