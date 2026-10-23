"""`aiplatform logs` — read deployed Cloud Run logs for the v6 backend.

The v6 backend runs as the ``sidecar`` container inside the
``platform-frontend`` Cloud Run service (the Next.js UI is ``main``). This
wraps the gcloud logging filter + project/service/container lookup so you don't
hand-build the incantation every time you need to diagnose a deployed turn.

``logs`` is a group whose bare invocation is the original command, so
``aiplatform --env dev logs -g Traceback`` is unchanged. ``logs client``
(FRONTEND-OBS M4) is the browser side: the events ``frontend/src/lib/logger.ts``
ships to ``POST /api/client-logs`` are re-emitted by the BACKEND container as
structured lines with ``jsonPayload.source="browser"``, so they are read from
the same place as backend logs but filtered on ``jsonPayload``, not
``textPayload``. ``logs client --emit`` sends one synthetic event through the
real authenticated route — it is what keeps ``scripts/verify-client-logs.sh``
thin, because the CLI already carries the token.

Requires the `gcloud` CLI on PATH, authenticated with access to the target
project. For `--env local`, use `make logs` / .dev-logs instead.
"""

from __future__ import annotations

import datetime as _dt
import json
import secrets
import shutil
import subprocess
from typing import Any

import click

from aiplatform.config import load_config
from aiplatform.http import AIPlatformClient, APIError

_CONFIG: dict[str, Any] = load_config()
_LOG_CFG: dict[str, Any] = _CONFIG.get("logging", {})

# The client says WARN (OTel LogRecord vocabulary); Cloud Logging says WARNING.
_CLOUD_SEVERITY = {"DEBUG": "DEBUG", "INFO": "INFO", "WARN": "WARNING", "ERROR": "ERROR"}


def _build_filter(service: str, container: str, patterns: tuple[str, ...]) -> str:
    # The app's stdout logs carry `labels.container_name` (= sidecar | main);
    # the `run.googleapis.com/container_name` variant is NOT set on Cloud Run
    # multi-container stdout entries, so filtering on it returns nothing.
    parts = [
        'resource.type="cloud_run_revision"',
        f'resource.labels.service_name="{service}"',
        f'labels.container_name="{container}"',
    ]
    if patterns:
        ors = " OR ".join(f'textPayload:"{p}"' for p in patterns)
        parts.append(f"({ors})")
    return " AND ".join(parts)


def _build_browser_filter(
    service: str,
    container: str,
    *,
    uid: str | None = None,
    session: str | None = None,
    severity: str | None = None,
    patterns: tuple[str, ...] = (),
) -> str:
    """Filter for browser events: structured lines, so every clause is jsonPayload."""
    parts = [
        'resource.type="cloud_run_revision"',
        f'resource.labels.service_name="{service}"',
        f'labels.container_name="{container}"',
        'jsonPayload.source="browser"',
    ]
    if uid:
        parts.append(f'jsonPayload.uid="{uid}"')
    if session:
        parts.append(f'jsonPayload.sessionId="{session}"')
    if severity:
        parts.append(f"severity>={_CLOUD_SEVERITY[severity]}")
    if patterns:
        # An ERROR's message carries the stack; an INFO's message is just the
        # event name, and its detail lives in errorMessage. Match all three so
        # a nonce is found whichever severity carried it.
        ors = " OR ".join(
            f'jsonPayload.message:"{p}" OR jsonPayload.event:"{p}" OR jsonPayload.errorMessage:"{p}"' for p in patterns
        )
        parts.append(f"({ors})")
    return " AND ".join(parts)


def _require_gcloud() -> None:
    if shutil.which("gcloud") is None:
        raise click.UsageError(
            "gcloud not found on PATH. Install the Cloud SDK: https://cloud.google.com/sdk/docs/install"
        )


def _resolve_target(env: str, project: str | None, service: str | None, container: str) -> tuple[str, str, str]:
    proj = project or _LOG_CFG.get("projects", {}).get(env)
    if not proj:
        raise click.UsageError(
            f"No GCP project configured for env '{env}'. Add logging.projects.{env} to cli/config.yaml or pass --project."
        )
    svc = service or _LOG_CFG.get("service", "platform-frontend")
    container_name = _LOG_CFG.get("containers", {}).get(container) or ("sidecar" if container == "backend" else "main")
    return proj, svc, container_name


def _gcloud_read(log_filter: str, proj: str, since: str, limit: int, fmt: str) -> str:
    cmd = [
        "gcloud",
        "logging",
        "read",
        log_filter,
        f"--project={proj}",
        f"--freshness={since}",
        f"--limit={limit}",
        f"--format={fmt}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException("gcloud logging read timed out after 60s.") from exc
    if result.returncode != 0:
        raise click.ClickException(f"gcloud logging read failed (exit {result.returncode}):\n{result.stderr.strip()}")
    return result.stdout.strip()


@click.group("logs", invoke_without_command=True)
@click.option(
    "--container",
    type=click.Choice(["backend", "ui"]),
    default="backend",
    help="Which container (backend = FastAPI sidecar, ui = Next.js). Default backend.",
)
@click.option(
    "--grep", "-g", "patterns", multiple=True, help="Only lines containing this text (repeatable; OR'd across --grep)."
)
@click.option("--since", default="30m", help="How far back (gcloud --freshness): 10m, 2h, 1d. Default 30m.")
@click.option("--limit", "-n", default=50, help="Max entries. Default 50.")
@click.option("--project", default=None, help="Override the GCP project id.")
@click.option("--service", default=None, help="Override the Cloud Run service name.")
@click.option("--raw", is_flag=True, help="Print full JSON entries instead of timestamp + textPayload.")
@click.pass_context
def logs(
    ctx: click.Context,
    container: str,
    patterns: tuple[str, ...],
    since: str,
    limit: int,
    project: str | None,
    service: str | None,
    raw: bool,
) -> None:
    """Read deployed Cloud Run logs for the v6 backend (or UI) container.

    Resolves project/service/container for --env and runs `gcloud logging read`.
    With a subcommand (`logs client`), this callback only parses options.

    Examples:

        aiplatform --env dev logs -g "doc loader" -g extract_ppa
        aiplatform --env dev logs --container ui --since 1h
        aiplatform --env dev logs -g Traceback -n 100
    """
    if ctx.invoked_subcommand is not None:
        return
    env = ctx.obj["env"]
    if env == "local":
        raise click.UsageError(
            "`logs` reads Cloud Run logs; local runs log to .dev-logs/backend.log — use `make logs`."
        )
    _require_gcloud()
    proj, svc, container_name = _resolve_target(env, project, service, container)
    log_filter = _build_filter(svc, container_name, patterns)
    fmt = "json" if raw else "value(timestamp, textPayload)"
    click.secho(f"# {svc}/{container_name} @ {proj} — last {since}, ≤{limit}", fg="cyan")
    out = _gcloud_read(log_filter, proj, since, limit, fmt)
    click.echo(out if out else "(no matching log entries)")


def _probe_events(nonce: str, severity: str, count: int, forge_uid: str | None) -> list[dict[str, Any]]:
    """Synthetic events for `--emit`. Shaped to sit under every backend cap."""
    now = _dt.datetime.now(_dt.UTC).isoformat()
    events: list[dict[str, Any]] = []
    for i in range(count):
        attrs: dict[str, Any] = {
            "event": "verify_probe",
            "route": "/cli/logs/client",
            "skillId": "verify-client-logs",
            "attempt": i + 1,
            "errorName": "VerifyProbe",
            "errorMessage": f"verify-client-logs probe {nonce}",
            # Error Reporting groups on the frames in `message`; the backend
            # puts this stack there for ERROR severity.
            "stack": (
                f"VerifyProbe: verify-client-logs probe {nonce}\n"
                "    at aiplatform.logs.client (cli/aiplatform/commands/logs.py)"
            ),
        }
        if forge_uid:
            # A negative probe: the server must log the TOKEN's uid, not these.
            attrs["uid"] = forge_uid
            attrs["userId"] = forge_uid
        events.append({"timestamp": now, "severityText": severity, "body": "verify_probe", "attributes": attrs})
    return events


@logs.command("client")
@click.option("--since", default="15m", help="How far back (gcloud --freshness). Default 15m.")
@click.option("--limit", "-n", default=50, help="Max entries. Default 50.")
@click.option("--uid", default=None, help="Only events logged for this uid (the TOKEN's uid, never the body's).")
@click.option("--session", default=None, help="Only events carrying this sessionId.")
@click.option(
    "--severity",
    type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"]),
    default=None,
    help="Read: minimum severity. Emit: the severity of the probe (default ERROR).",
)
@click.option("--grep", "-g", "patterns", multiple=True, help="Match message / event / errorMessage (repeatable).")
@click.option("--project", default=None, help="Override the GCP project id.")
@click.option("--service", default=None, help="Override the Cloud Run service name.")
@click.option("--raw", is_flag=True, help="Print full JSON entries.")
@click.option("--emit", is_flag=True, help="Send ONE synthetic event through POST /api/client-logs instead of reading.")
@click.option("--nonce", default=None, help="Emit: tag the probe with this string (default: random).")
@click.option("--count", default=1, show_default=True, help="Emit: number of events in the batch (51 proves the cap).")
@click.option("--forge-uid", default=None, help="Emit: put this uid in the body (the server must ignore it).")
@click.pass_context
def logs_client(
    ctx: click.Context,
    since: str,
    limit: int,
    uid: str | None,
    session: str | None,
    severity: str | None,
    patterns: tuple[str, ...],
    project: str | None,
    service: str | None,
    raw: bool,
    emit: bool,
    nonce: str | None,
    count: int,
    forge_uid: str | None,
) -> None:
    """Browser events (FRONTEND-OBS): read them from Cloud Logging, or emit one.

    Examples:

        aiplatform --env dev logs client --since 15m --severity ERROR
        aiplatform --env dev logs client --uid <uid> --session <id> --raw
        aiplatform --env dev logs client --emit              # one ERROR probe, prints the nonce
        aiplatform --env dev logs client --emit --severity INFO --count 51
    """
    env = ctx.obj["env"]

    if emit:
        probe_nonce = nonce or secrets.token_hex(6)
        sev = severity or "ERROR"
        client = AIPlatformClient(env=env)
        try:
            response = client.post(
                "/api/client-logs", json={"events": _probe_events(probe_nonce, sev, count, forge_uid)}
            )
        except APIError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(json.dumps({"nonce": probe_nonce, "severity": sev, "count": count, "response": response}))
        return

    if env == "local":
        raise click.UsageError(
            "`logs client` reads Cloud Run logs; for a local backend watch .dev-logs/backend.log "
            'for lines with "source": "browser".'
        )
    _require_gcloud()
    proj, svc, container_name = _resolve_target(env, project, service, "backend")
    log_filter = _build_browser_filter(
        svc, container_name, uid=uid, session=session, severity=severity, patterns=patterns
    )
    fmt = "json" if raw else "value(timestamp, severity, jsonPayload.uid, jsonPayload.event, jsonPayload.message)"
    click.secho(f"# browser events via {svc}/{container_name} @ {proj} — last {since}, ≤{limit}", fg="cyan")
    out = _gcloud_read(log_filter, proj, since, limit, fmt)
    click.echo(out if out else "(no matching browser events)")
