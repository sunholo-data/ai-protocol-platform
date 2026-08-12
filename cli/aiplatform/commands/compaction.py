"""`aiplatform compaction` — replay a compaction over a recorded session.

The tuning loop for conversation compaction. Everything that went wrong in this
subsystem was a MEASUREMENT failure: a canary that passed under both a working
and a broken config, a probe whose synthetic fixture was degenerate, a threshold
change unit tests blessed and a live run disproved. Both remaining unknowns —
why ~22% of compactions return nothing, and whether a fact survives one — need
real conversations rather than invented ones.

`replay` answers "what would compaction do to THIS session", read-only.
`compare` runs it twice with different knobs so a change can be judged by
diffing outputs rather than by argument.

    aiplatform compaction replay <session-id> --user-id <uid>
    aiplatform compaction replay <session-id> --user-id <uid> --selection-only
    aiplatform compaction compare <session-id> --user-id <uid> --retention 20,60
    aiplatform compaction recompact <session-id> --user-id <uid> --dry-run

`recompact` is the SECOND PASS (design 1e): re-derive the compacted span from
raw events and append a superseding summary. Unlike replay it MUTATES the
session — `--dry-run` first is the intended workflow.

Endpoints: POST /api/admin/compaction/replay (never mutates) and
/api/admin/compaction/recompact (mutates unless dry_run); both Aitana-admin
only. See docs/projects/compaction/README.md.
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient, APIError


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def compaction() -> None:
    """Replay and compare conversation compaction (read-only)."""


def _post(ctx: click.Context, payload: dict) -> dict:
    client = _client(ctx)
    return client.post("/api/admin/compaction/replay", json=payload)


def _render(res: dict, *, show_summary: bool = True) -> None:
    click.echo(f"  events in session : {res.get('total_events')}")
    click.echo(f"  prior compactions : {res.get('existing_compactions')}")
    click.echo(f"  selected to compact: {res.get('selected_events')}  ({res.get('input_chars')} chars)")

    if res.get("declined"):
        # The headline diagnostic. A decline in production means history was NOT
        # compacted and the model call was wasted — invisible without this.
        click.secho("  RESULT            : DECLINED (no summary produced)", fg="red", bold=True)
    elif res.get("selected_events"):
        ratio = ""
        if res.get("input_chars") and res.get("summary_chars"):
            ratio = f"  ({res['input_chars'] / res['summary_chars']:.1f}x compression)"
        click.secho(
            f"  RESULT            : {res.get('summary_chars')} chars in {res.get('elapsed_ms')} ms{ratio}",
            fg="green",
        )

    for note in res.get("notes") or []:
        click.echo(f"  note: {note}")

    if show_summary and res.get("summary"):
        click.echo("\n--- summary ---")
        click.echo(res["summary"])


@compaction.command("replay")
@click.argument("session_id")
@click.option("--user-id", required=True, help="ADK user id owning the session (the Firebase uid).")
@click.option("--retention", type=int, default=None, help="Override event_retention_size for this replay.")
@click.option("--model", "model_ref", default=None, help="Summariser model tier or registry id.")
@click.option("--prompt-file", type=click.Path(exists=True), default=None, help="File with an alternative prompt.")
@click.option(
    "--selection-only",
    is_flag=True,
    help="Report WHAT would be compacted without calling a model. Fast and free — answers 'would this "
    "session compact at all' on its own.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the raw result as JSON.")
@click.pass_context
def replay(
    ctx: click.Context,
    session_id: str,
    user_id: str,
    retention: int | None,
    model_ref: str | None,
    prompt_file: str | None,
    selection_only: bool,
    as_json: bool,
) -> None:
    """Replay a compaction over SESSION_ID without mutating it."""
    payload: dict = {"session_id": session_id, "user_id": user_id, "summarize": not selection_only}
    if retention is not None:
        payload["event_retention_size"] = retention
    if model_ref:
        payload["model_ref"] = model_ref
    if prompt_file:
        with open(prompt_file, encoding="utf-8") as fh:
            payload["prompt_template"] = fh.read()

    try:
        res = _post(ctx, payload)
    except APIError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(_json.dumps(res, indent=2))
        return
    click.echo(f"session {session_id}")
    _render(res)


@compaction.command("recompact")
@click.argument("session_id")
@click.option("--user-id", required=True, help="ADK user id owning the session (the Firebase uid).")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Summarise but do NOT append — shows what the second pass would write. Run this first.",
)
@click.option(
    "--enqueue",
    is_flag=True,
    help="Schedule a REAL zero-delay Cloud Tasks delivery instead of running in-process — verifies the "
    "queue→OIDC→route chain end-to-end. Check the session afterwards with `compaction replay`.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the raw result as JSON.")
@click.option("--yes", is_flag=True, help="Skip the mutation confirmation prompt.")
@click.pass_context
def recompact(
    ctx: click.Context, session_id: str, user_id: str, dry_run: bool, enqueue: bool, as_json: bool, yes: bool
) -> None:
    """Second-pass recompact SESSION_ID from raw events (MUTATES unless --dry-run).

    Appends a compaction event that supersedes every existing summary via
    ADK's subsume rule; the next model request and the next live compaction
    both use the new one. A no-op is reported, not invented — look for
    `nothing_to_improve` / `declined` in the output.
    """
    if not dry_run and not yes:
        click.confirm(
            f"Append a superseding summary to session {session_id}? (replay/--dry-run first is the workflow)",
            abort=True,
        )
    payload = {"session_id": session_id, "user_id": user_id, "dry_run": dry_run, "enqueue": enqueue}
    try:
        res = _client(ctx).post("/api/admin/compaction/recompact", json=payload)
    except APIError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(_json.dumps(res, indent=2))
        return

    if enqueue:
        if res.get("enqueued"):
            click.secho(f"  task ENQUEUED (zero delay) for compaction end {res.get('compaction_end_ts')}", fg="green")
            click.echo(
                "  watch: gcloud tasks queues describe platform-compaction; then `compaction replay` the session"
            )
        else:
            click.secho("  NOT enqueued — flag off, env unconfigured, or duplicate task (see backend logs)", fg="red")
        return

    click.echo(f"session {session_id}")
    click.echo(f"  events in session : {res.get('total_events')}")
    click.echo(f"  prior compactions : {res.get('prior_compactions')}")
    click.echo(f"  selected raw      : {res.get('selected_events')}  ({res.get('input_chars')} chars)")
    if res.get("stale"):
        click.secho("  RESULT            : STALE — newer session activity; nothing done", fg="yellow")
    elif res.get("nothing_to_improve"):
        click.secho("  RESULT            : NOTHING TO IMPROVE", fg="yellow")
    elif res.get("declined"):
        click.secho("  RESULT            : DECLINED (no summary produced)", fg="red", bold=True)
    else:
        verb = "would append" if res.get("dry_run") else "APPENDED"
        rng = f"range {res.get('start_timestamp')} → {res.get('end_timestamp')}"
        click.secho(
            f"  RESULT            : {verb} {res.get('summary_chars')} chars in {res.get('elapsed_ms')} ms ({rng})",
            fg="green",
        )
    for note in res.get("notes") or []:
        click.echo(f"  note: {note}")
    if res.get("summary"):
        click.echo("\n--- second-pass summary ---")
        click.echo(res["summary"])


@compaction.command("compare")
@click.argument("session_id")
@click.option("--user-id", required=True, help="ADK user id owning the session.")
@click.option(
    "--retention",
    default=None,
    help="Comma-separated retention sizes to compare, e.g. 20,60. Retention gates whether compaction "
    "fires at all, so it is usually the first knob worth sweeping.",
)
@click.option("--models", default=None, help="Comma-separated model tiers to compare, e.g. lite,pro.")
@click.pass_context
def compare(ctx: click.Context, session_id: str, user_id: str, retention: str | None, models: str | None) -> None:
    """Run the same session under several settings and print each result.

    Judge a compaction change by diffing real outputs, not by argument. Each arm
    costs a model call, so sweep one dimension at a time.
    """
    if not retention and not models:
        raise click.ClickException("give --retention and/or --models to compare")

    arms: list[tuple[str, dict]] = []
    base = {"session_id": session_id, "user_id": user_id, "summarize": True}
    for r in (retention or "").split(","):
        if r.strip():
            arms.append((f"retention={r.strip()}", {**base, "event_retention_size": int(r.strip())}))
    for m in (models or "").split(","):
        if m.strip():
            arms.append((f"model={m.strip()}", {**base, "model_ref": m.strip()}))

    for label, payload in arms:
        click.secho(f"\n=== {label} ===", bold=True)
        try:
            res = _post(ctx, payload)
        except APIError as exc:
            click.secho(f"  FAILED: {exc}", fg="red")
            continue
        # Summaries are long; compare on the metrics and replay a single arm to
        # read one in full.
        _render(res, show_summary=False)
