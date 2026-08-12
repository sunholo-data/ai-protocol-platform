"""`aiplatform deploy` — release identity and environment promotion.

v6.20.0, AIPLA #46/#47. Three commands covering the release path:

  * ``status``  — what is actually running in an env, BY DIGEST. This is the
    command that makes a promotion verifiable: if test and prod report the same
    backend digest, the promotion copied; if they differ, it rebuilt and is not
    a promotion.
  * ``promote`` — move a released version to the next env. Thin wrapper over
    ``scripts/promote-env.sh`` so there is ONE implementation of the promotion
    logic, not a shell one and a Python one that drift.
  * ``release`` — tag a commit and push the tag, which fires the env's release
    trigger.

Requires the `gcloud` CLI on PATH, authenticated against the target project.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import click
import yaml

_CONFIG: dict[str, Any] = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
_DEPLOY_CFG: dict[str, Any] = _CONFIG.get("deploy", {})

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMOTE_SCRIPT = REPO_ROOT / "scripts" / "promote-env.sh"

# Only these promotion edges exist. Deliberately NOT dev->prod: the point of
# the model is that prod receives exactly what test verified. Kept in step with
# the same table in scripts/promote-env.sh.
VALID_EDGES = {("dev", "test"), ("test", "prod")}


def _require_gcloud() -> None:
    if shutil.which("gcloud") is None:
        raise click.ClickException("gcloud CLI not found on PATH. Install the Google Cloud SDK first.")


def _project_for(env: str) -> str:
    projects = _DEPLOY_CFG.get("projects", {})
    project = projects.get(env)
    if not project:
        raise click.ClickException(
            f"No project configured for env '{env}'. Set deploy.projects.{env} in cli/aiplatform/config.yaml."
        )
    return str(project)


@click.group("deploy")
def deploy() -> None:
    """Release identity and environment promotion (dev -> test -> prod)."""


@deploy.command("status")
@click.option(
    "--env",
    "env_name",
    type=click.Choice(["dev", "test", "prod"]),
    required=True,
    help="Which environment to inspect.",
)
@click.option("--service", default=None, help="Override the Cloud Run service name.")
def status(env_name: str, service: str | None) -> None:
    """Show the live revision and per-container image DIGESTS for an env.

    Digest equality across two envs is the proof that a promotion copied the
    tested bytes rather than rebuilding them.
    """
    _require_gcloud()
    project = _project_for(env_name)
    service_name = service or _DEPLOY_CFG.get("service", "platform-frontend")
    region = _DEPLOY_CFG.get("region", "europe-west1")

    result = subprocess.run(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            service_name,
            f"--project={project}",
            f"--region={region}",
            "--format=value(status.latestReadyRevisionName,spec.template.spec.containers[].image)",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(f"gcloud failed: {result.stderr.strip()}")

    output = result.stdout.strip()
    if not output:
        raise click.ClickException(f"No output for {service_name} in {project} — does the service exist?")

    # gcloud joins the two projected fields with a tab, and the repeated
    # container images with semicolons.
    parts = output.split("\t")
    revision = parts[0] if parts else "(unknown)"
    images = parts[1].split(";") if len(parts) > 1 else []

    click.echo(f"env      : {env_name}")
    click.echo(f"project  : {project}")
    click.echo(f"service  : {service_name}")
    click.echo(f"revision : {revision}")
    for image in images:
        image = image.strip()
        if not image:
            continue
        name = image.rsplit("/", 1)[-1].split("@")[0].split(":")[0]
        if "@" in image:
            click.echo(f"  {name:<8} {image.split('@', 1)[1]}")
        else:
            # A tag rather than a digest means this env is NOT pinned — the
            # exact ambiguity M1 removed, so say so rather than printing it
            # as if it were an identity.
            click.echo(f"  {name:<8} NOT PINNED (tag: {image.rsplit(':', 1)[-1]})")


@deploy.command("promote")
@click.option("--from", "from_env", type=click.Choice(["dev", "test"]), required=True, help="Source environment.")
@click.option("--to", "to_env", type=click.Choice(["test", "prod"]), required=True, help="Target environment.")
@click.option("--version", required=True, help="Release tag to promote, e.g. v6.20.0. Must exist on origin.")
@click.option("--yes", "-y", is_flag=True, help="Actually run it. Without this, prints the plan and exits.")
def promote(from_env: str, to_env: str, version: str, yes: bool) -> None:
    """Promote a released version to the next environment.

    Copies the tested backend + toolbox images BY DIGEST and rebuilds only the
    frontend (NEXT_PUBLIC_* is compile-time-inlined, so a copied UI would carry
    the source env's Firebase project and API URLs).

    Runs as a Cloud Build TRIGGER at the tag, so your local working tree has no
    bearing on what ships.
    """
    if (from_env, to_env) not in VALID_EDGES:
        raise click.ClickException(
            f"'{from_env} -> {to_env}' is not a valid promotion edge. "
            "Valid: dev -> test, test -> prod. prod must receive what test verified."
        )
    if not PROMOTE_SCRIPT.is_file():
        raise click.ClickException(f"Promotion script not found at {PROMOTE_SCRIPT}")

    cmd = [
        str(PROMOTE_SCRIPT),
        "--from",
        from_env,
        "--to",
        to_env,
        "--version",
        version,
    ]
    if yes:
        cmd.append("--yes")

    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise click.ClickException(f"Promotion failed (exit {result.returncode}).")


@deploy.command("release")
@click.option("--version", required=True, help="Version tag to cut, e.g. v6.20.0.")
@click.option("--ref", default="HEAD", show_default=True, help="Git ref to tag.")
@click.option("--message", "-m", default=None, help="Annotation message. Defaults to the version.")
@click.option("--yes", "-y", is_flag=True, help="Actually tag and push. Without this, prints the plan and exits.")
def release(version: str, ref: str, message: str | None, yes: bool) -> None:
    """Tag a commit and push the tag, firing the env's release trigger."""
    annotation = message or version
    tag_cmd = ["git", "tag", "-a", version, ref, "-m", annotation]
    push_cmd = ["git", "push", "origin", version]

    if not yes:
        click.echo("DRY RUN — nothing has been changed. Would run:")
        click.echo(f"  {' '.join(tag_cmd)}")
        click.echo(f"  {' '.join(push_cmd)}")
        click.echo("\nRe-run with --yes to execute.")
        return

    for cmd in (tag_cmd, push_cmd):
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise click.ClickException(f"Command failed: {' '.join(cmd)}")
    click.echo(f"Tagged and pushed {version}.")
