"""`aiplatform logs` — read deployed Cloud Run logs for the v6 backend.

The v6 backend runs as the ``sidecar`` container inside the
``platform-frontend`` Cloud Run service (the Next.js UI is ``main``). This
wraps the gcloud logging filter + project/service/container lookup so you don't
hand-build the incantation every time you need to diagnose a deployed turn.

Requires the `gcloud` CLI on PATH, authenticated with access to the target
project. For `--env local`, use `make logs` / .dev-logs instead.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import click
import yaml

_CONFIG: dict[str, Any] = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
_LOG_CFG: dict[str, Any] = _CONFIG.get("logging", {})


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


@click.command("logs")
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

    Examples:

        aiplatform --env dev logs -g "doc loader" -g extract_ppa
        aiplatform --env dev logs --container ui --since 1h
        aiplatform --env dev logs -g Traceback -n 100
    """
    env = ctx.obj["env"]
    if env == "local":
        raise click.UsageError(
            "`logs` reads Cloud Run logs; local runs log to .dev-logs/backend.log — use `make logs`."
        )
    if shutil.which("gcloud") is None:
        raise click.UsageError(
            "gcloud not found on PATH. Install the Cloud SDK: https://cloud.google.com/sdk/docs/install"
        )

    proj = project or _LOG_CFG.get("projects", {}).get(env)
    if not proj:
        raise click.UsageError(
            f"No GCP project configured for env '{env}'. Add logging.projects.{env} to cli/config.yaml or pass --project."
        )
    svc = service or _LOG_CFG.get("service", "platform-frontend")
    container_name = _LOG_CFG.get("containers", {}).get(container) or ("sidecar" if container == "backend" else "main")

    log_filter = _build_filter(svc, container_name, patterns)
    fmt = "json" if raw else "value(timestamp, textPayload)"
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

    click.secho(f"# {svc}/{container_name} @ {proj} — last {since}, ≤{limit}", fg="cyan")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException("gcloud logging read timed out after 60s.") from exc

    if result.returncode != 0:
        raise click.ClickException(f"gcloud logging read failed (exit {result.returncode}):\n{result.stderr.strip()}")

    out = result.stdout.strip()
    click.echo(out if out else "(no matching log entries)")
