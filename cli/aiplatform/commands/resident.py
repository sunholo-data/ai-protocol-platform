"""`aiplatform resident` — talk to a resident agent over A2A.

WHY THIS TALKS TO THE AGENT DIRECTLY AND NOT THROUGH THE BACKEND

Every other command here is a client of our backend, and for product features
that is right. This one is deliberately the operator path: the resident runs in
a different project under a different identity, and going through the backend
to reach it would mean a failure could be the instance, the backend, the SA
grant, the client or the network — five suspects for one symptom. Asking the
peer directly leaves one.

That is the same split as `verify_resident_live.py` (does the PLATFORM's client
work) versus `resident-instance.sh verify` in ailang-multivac (does the AGENT
work). Run them apart and a failure says which half broke.

AUTH

The resident verifies Google ID tokens itself against its own caller allowlist,
because the Cloud Run instances Preview does not enforce run.invoker at the
edge. So this needs a token whose audience is the agent's URL:

  1. --token / $AIPLATFORM_RESIDENT_TOKEN
  2. --impersonate SA / $AIPLATFORM_RESIDENT_IMPERSONATE (local dev: ADC on a
     developer machine is a USER credential and cannot mint an ID token, so
     impersonating a service account on the allowlist is the working path)
  3. plain `gcloud auth print-identity-token --audiences=...` (service-account
     ADC or a metadata server)

CONVERSATIONS

A2A's contextId is the thread key: reuse one to continue, use a new one to
start over. `ask` mints a fresh thread unless you pass --context; `chat` keeps
one for the length of the session and prints it, so it can be resumed later.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import click
import httpx

TERMINAL = {"completed", "failed", "canceled", "rejected", "input-required"}
REGISTRY_EXT = "resident-registry"


class ResidentError(click.ClickException):
    """A peer could not be reached, or refused. Raised, never returned as an
    empty answer — a caller that reads silence as 'nothing to say' is the
    failure NEVER SILENT exists to prevent."""


def _token(url: str, token: str, impersonate: str) -> str:
    token = token or os.environ.get("AIPLATFORM_RESIDENT_TOKEN", "")
    if token:
        return token
    impersonate = impersonate or os.environ.get("AIPLATFORM_RESIDENT_IMPERSONATE", "")
    cmd = ["gcloud", "auth", "print-identity-token", f"--audiences={url}"]
    if impersonate:
        cmd += [f"--impersonate-service-account={impersonate}", "--include-email"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise ResidentError("gcloud is not on PATH; pass --token instead.") from exc
    except subprocess.CalledProcessError as exc:
        hint = (
            ""
            if impersonate
            else (
                "\nADC on a developer machine is a user credential and cannot mint an ID "
                "token. Pass --impersonate with a service account on the agent's allowlist."
            )
        )
        raise ResidentError(f"could not mint an ID token for {url}:\n{exc.stderr.strip()}{hint}") from exc
    return out.stdout.strip()


def _rpc(url: str, token: str, method: str, params: dict) -> dict:
    try:
        r = httpx.post(
            f"{url.rstrip('/')}/a2a",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=90.0,
        )
    except httpx.HTTPError as exc:
        raise ResidentError(f"{method} to {url} failed: {exc}") from exc
    if r.status_code == 401:
        raise ResidentError(
            f"{url} refused the token (401). The agent checks the caller against its own "
            "allowlist; --impersonate a service account that is on it."
        )
    if r.status_code >= 400:
        raise ResidentError(f"{method} returned HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if "error" in body:
        raise ResidentError(f"{method} refused: {body['error'].get('message', body['error'])}")
    return body.get("result") or {}


def _card(url: str, token: str) -> dict:
    try:
        r = httpx.get(
            f"{url.rstrip('/')}/.well-known/agent.json",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise ResidentError(f"agent card unreachable at {url}: {exc}") from exc
    return r.json()


def _registered_models(card: dict) -> list[str]:
    """From capabilities.extensions — A2A's AgentCard has no `metadata` field,
    so anything published there is dropped by compliant clients."""
    for ext in card.get("capabilities", {}).get("extensions") or []:
        if REGISTRY_EXT in ext.get("uri", ""):
            return (ext.get("params") or {}).get("registeredModels") or []
    return []


def _answer(task: dict) -> str:
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("kind") == "text" and part.get("text"):
                return str(part["text"])
    return ""


def _turn(url: str, token: str, text: str, context_id: str, model: str, timeout: float) -> dict:
    """Send one message and poll to a terminal state.

    The agent answers message/send immediately with a `submitted` task and
    works in the background, so returning here without polling would report
    only that the request was accepted.
    """
    message = {
        "role": "user",
        "kind": "message",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": text}],
        "contextId": context_id,
    }
    meta = {"model": model} if model else {}
    task = _rpc(url, token, "message/send", {"message": message, "metadata": meta})
    task_id = task.get("id")
    if not task_id:
        raise ResidentError(f"no task id in response: {json.dumps(task)[:200]}")

    deadline = time.monotonic() + timeout
    state = (task.get("status") or {}).get("state", "")
    while state not in TERMINAL:
        if time.monotonic() >= deadline:
            raise ResidentError(f"task {task_id} did not finish within {timeout:g}s (last state: {state})")
        time.sleep(2.0)
        task = _rpc(url, token, "tasks/get", {"id": task_id})
        state = (task.get("status") or {}).get("state", "")
    return task


def _resolve_url(url: str) -> str:
    url = url or os.environ.get("AIPLATFORM_RESIDENT_URL", "")
    if not url:
        raise ResidentError(
            "no agent URL: pass --url or set AIPLATFORM_RESIDENT_URL.\n"
            "It is the instance's base https URL, which is also the audience its tokens must carry."
        )
    return url.rstrip("/")


_url_opt = click.option("--url", default="", help="agent base URL (or $AIPLATFORM_RESIDENT_URL)")
_auth_opts = [
    click.option("--impersonate", default="", help="service account to mint the ID token as"),
    click.option("--token", default="", help="explicit ID token (skips gcloud)"),
]


def _with_auth(f):
    for opt in reversed(_auth_opts):
        f = opt(f)
    return f


@click.group()
def resident() -> None:
    """Talk to a resident agent over A2A (operator path — goes direct, not via the backend)."""


@resident.command("card")
@_url_opt
@_with_auth
def card_cmd(url: str, impersonate: str, token: str) -> None:
    """Show the agent card: who it is, and what it will run."""
    url = _resolve_url(url)
    c = _card(url, _token(url, token, impersonate))
    click.echo(f"name        : {c.get('name')}")
    click.echo(f"url         : {c.get('url')}")
    click.echo(f"streaming   : {c.get('capabilities', {}).get('streaming')}")
    models = _registered_models(c)
    click.echo(f"models      : {', '.join(models) if models else '(none advertised)'}")
    for ext in c.get("capabilities", {}).get("extensions") or []:
        click.echo(f"extension   : {ext.get('uri')}")


@resident.command("ask")
@click.argument("message")
@_url_opt
@_with_auth
@click.option("--context", "context_id", default="", help="thread to continue; omit to start a new one")
@click.option("--model", default="", help="model ref; defaults to the agent's sole registered model")
@click.option("--timeout", default=240.0, show_default=True, help="seconds to wait for the answer")
@click.option("--json", "as_json", is_flag=True, help="print the raw task JSON")
def ask_cmd(message, url, impersonate, token, context_id, model, timeout, as_json) -> None:
    """Ask the agent one question and print its answer."""
    url = _resolve_url(url)
    tok = _token(url, token, impersonate)
    context_id = context_id or f"cli-{uuid.uuid4().hex[:12]}"
    task = _turn(url, tok, message, context_id, model, timeout)
    if as_json:
        click.echo(json.dumps(task, indent=2))
        return
    state = (task.get("status") or {}).get("state")
    if state != "completed":
        # Not an answer. Saying so beats printing a fragment that reads like one.
        click.echo(f"agent did not finish (state: {state})", err=True)
        partial = _answer(task)
        if partial:
            click.echo(partial, err=True)
        sys.exit(1)
    click.echo(_answer(task))
    click.echo(f"\n[thread {context_id}]", err=True)


def _story_events(task: dict) -> tuple[list[dict], bool]:
    """The `events` artifact, or ([], False).

    Absent is the NORMAL case, not an error: an instance built before M7 never
    writes one, and the run is then simply prose. Every hop of this feature is
    additive in both directions.
    """
    for artifact in task.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("artifactId") != "events":
            continue
        for part in artifact.get("parts") or []:
            data = part.get("data") if isinstance(part, dict) else None
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                return [e for e in data["events"] if isinstance(e, dict)], bool(data.get("truncated"))
    return [], False


@resident.command("story")
@click.argument("task_id")
@_url_opt
@_with_auth
@click.option("--json", "as_json", is_flag=True, help="print the raw events array")
def story_cmd(task_id, url, impersonate, token, as_json) -> None:
    """Print what the agent DID on a run, step by step (M7).

    The same events the workbench surface is drawn from, in a terminal. An
    operator debugging a run should not have to open a browser to see which
    commands it ran, and the artifact is readable by any A2A client — this is
    just one.
    """
    url = _resolve_url(url)
    task = _rpc(url, _token(url, token, impersonate), "tasks/get", {"id": task_id})
    events, truncated = _story_events(task)

    if as_json:
        click.echo(json.dumps(events, indent=2))
        return

    state = (task.get("status") or {}).get("state", "unknown")
    click.echo(f"task {task_id} — {state}")
    if not events:
        # SAY WHICH of the two reasons it is. "No events" that could mean
        # either "the agent did nothing" or "this instance predates the
        # feature" is the kind of silence this milestone exists to remove.
        click.echo(
            "\nno session events on this task — either the run made no tool calls, "
            "or the instance predates M7 and does not write the artifact.",
            err=True,
        )
        answer = _answer(task)
        if answer:
            click.echo(f"\nanswer:\n{answer}")
        return

    if truncated:
        click.echo("(earlier steps dropped — this run was longer than the story keeps)")
    click.echo("")
    for e in events:
        kind = e.get("type")
        if kind == "tool_use":
            click.echo(f"  → {e.get('tool', 'tool')}  {_one_line(e.get('input'), 100)}")
        elif kind == "tool_result":
            out = _one_line(e.get("output"), 160)
            # An empty result is a FACT. Printing nothing makes a tool that
            # returned nothing look like one that returned something.
            click.echo(f"  ← {out or '(no output)'}")
        elif kind == "text":
            click.echo(f"  · {_one_line(e.get('text'), 160)}")
        elif kind == "turn_end":
            usage = e.get("usage")
            click.echo(f"  ─ turn complete{f'  {usage}' if usage else ''}")

    files = _files_touched(events)
    if files:
        click.echo("\nfiles:")
        for path, verb in files:
            click.echo(f"  {verb:8} {path}")

    answer = _answer(task)
    if answer:
        click.echo(f"\nanswer:\n{answer}")


def _one_line(v, limit: int) -> str:
    t = " ".join(str(v or "").split())
    return t if len(t) <= limit else f"{t[:limit]}…"


_TOOL_VERB = {"read": "read", "view": "read", "write": "written", "edit": "edited", "str_replace": "edited"}
_VERB_RANK = {"read": 0, "written": 1, "edited": 2}
_PATH_KEYS = ("path", "file_path", "filePath", "filename", "file")


def _files_touched(events: list[dict]) -> list[tuple[str, str]]:
    """Mirrors backend/adk/a2ui_resident_render.files_touched.

    Duplicated rather than imported: the CLI is installed standalone and does
    not have the backend on its path. The shapes it reads are the artifact's,
    which is a published contract, so the two cannot drift silently — a change
    to the artifact breaks both.
    """
    seen: dict[str, str] = {}
    order: list[str] = []
    for e in events:
        if e.get("type") != "tool_use":
            continue
        verb = _TOOL_VERB.get(str(e.get("tool") or "").lower())
        if not verb:
            continue
        try:
            args = json.loads(e.get("input") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(args, dict):
            continue
        path = next((str(args[k]) for k in _PATH_KEYS if isinstance(args.get(k), str) and args[k]), "")
        if not path:
            continue
        if path not in seen:
            order.append(path)
            seen[path] = verb
        elif _VERB_RANK[verb] > _VERB_RANK[seen[path]]:
            seen[path] = verb
    return [(p, seen[p]) for p in order]


@resident.command("chat")
@_url_opt
@_with_auth
@click.option("--context", "context_id", default="", help="resume an existing thread")
@click.option("--model", default="", help="model ref; defaults to the agent's sole registered model")
@click.option("--timeout", default=240.0, show_default=True)
def chat_cmd(url, impersonate, token, context_id, model, timeout) -> None:
    """Hold a conversation. The agent remembers it between turns."""
    url = _resolve_url(url)
    tok = _token(url, token, impersonate)
    context_id = context_id or f"cli-{uuid.uuid4().hex[:12]}"
    c = _card(url, tok)
    click.echo(f"{c.get('name')} — thread {context_id}")
    click.echo("(ctrl-d or /exit to leave; the thread id above resumes it later)\n")
    while True:
        try:
            line = click.prompt("you", prompt_suffix="> ")
        except (EOFError, click.Abort):
            click.echo()
            break
        if line.strip() in ("/exit", "/quit"):
            break
        if not line.strip():
            continue
        try:
            task = _turn(url, tok, line, context_id, model, timeout)
        except ResidentError as exc:
            click.echo(f"  ! {exc}", err=True)
            continue
        state = (task.get("status") or {}).get("state")
        if state != "completed":
            click.echo(f"  ! agent did not finish (state: {state})", err=True)
            continue
        click.echo(f"\n{_answer(task)}\n")


# ── admin (v6.40.0 M14) ──────────────────────────────────────────────────────
# UNLIKE the commands above, these go THROUGH the backend. That is not an
# inconsistency: `ask`/`chat` deliberately talk to the peer so a failure has one
# suspect, whereas the admin surface is about the platform's own records and
# authority, and the platform is where tenant scoping and the audit trail live.
# Routing admin through the peer would put spend decisions somewhere with no
# notion of who is asking.

from aiplatform.http import AIPlatformClient  # noqa: E402


def _api(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@resident.group("admin")
def admin() -> None:
    """Who may have a resident agent, what state it is in, what it costs."""


@admin.command("list")
@click.pass_context
def admin_list(ctx: click.Context) -> None:
    """Every resident agent within your admin scope."""
    rows = _api(ctx).get("/api/admin/residents") or []
    if not rows:
        click.echo("No resident agents in scope.")
        return
    click.echo(f"{'USER':<16} {'STATE':<13} {'BUDGET':>8}  LAST USED")
    for r in rows:
        budget = f"${r.get('budgetUsd', 0):g}"
        click.echo(f"{r.get('userHash', ''):<16} {r.get('state', ''):<13} {budget:>8}  {r.get('lastUsedAt') or '-'}")


@admin.command("status")
@click.argument("email")
@click.pass_context
def admin_status(ctx: click.Context, email: str) -> None:
    """One user's resident agent: state, budget, and why if it is broken."""
    r = _api(ctx).get(f"/api/admin/residents/{email}")
    click.echo(f"user    : {email} ({r.get('userHash')})")
    click.echo(f"state   : {r.get('state')}")
    click.echo(f"instance: {r.get('instance') or '-'}")
    click.echo(f"url     : {r.get('url') or '-'}")
    click.echo(f"budget  : ${r.get('budgetUsd', 0):g}/month")
    click.echo(f"last use: {r.get('lastUsedAt') or '-'}")
    # Surfaced rather than buried: a failed provision that reports no reason is
    # the failure this design keeps meeting.
    if r.get("error"):
        click.echo(f"error   : {r['error']}")


@admin.command("budget")
@click.argument("email")
@click.option("--limit", type=float, default=None, help="new monthly cap in USD; omit to just read")
@click.pass_context
def admin_budget(ctx: click.Context, email: str, limit: float | None) -> None:
    """Show or change a user's monthly model budget."""
    if limit is None:
        r = _api(ctx).get(f"/api/admin/residents/{email}")
        click.echo(f"{email}: ${r.get('budgetUsd', 0):g}/month")
        return
    r = _api(ctx).patch(f"/api/admin/residents/{email}/budget", json={"budgetUsd": limit})
    click.echo(f"{email}: budget now ${r.get('budgetUsd', 0):g}/month")
