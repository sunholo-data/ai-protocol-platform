"""`aiplatform project` — keep a project in step with a GitHub folder.

The headless side of a project's sources (docs/design/private/github-sources.md),
so "add a source → preview → sync → push → sync" can be run and checked from a
terminal. Acts as the signed-in user (AIPLATFORM_ID_TOKEN), on their own projects.

Endpoints used:
    GET    /api/projects                              — resolve a project name
    GET    /api/projects/{id}/sources                 — list
    POST   /api/projects/{id}/sources                 — add
    DELETE /api/projects/{id}/sources/{sourceId}      — remove (documents stay)
    POST   /api/projects/{id}/sources/{sourceId}/sync — preview or sync
"""

from __future__ import annotations

import click

from aiplatform.http import AIPlatformClient

# One sync call runs for up to ~4 minutes on the server; allow for the response.
_SYNC_TIMEOUT = 330.0
_MAX_CONTINUATIONS = 20


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


def _project_id(client: AIPlatformClient, name_or_id: str) -> str:
    projects = (client.get("/api/projects") or {}).get("projects") or []
    for p in projects:
        if p["id"] == name_or_id:
            return p["id"]
    matches = [p for p in projects if p["name"].casefold() == name_or_id.strip().casefold()]
    if not matches:
        raise click.ClickException(f"No project called {name_or_id!r}. `aiplatform project list` shows yours.")
    return matches[0]["id"]


def _sources(client: AIPlatformClient, pid: str) -> list[dict]:
    return (client.get(f"/api/projects/{pid}/sources") or {}).get("sources") or []


def _source_id(client: AIPlatformClient, pid: str, given: str) -> str:
    sources = _sources(client, pid)
    if given:
        if not any(s["id"] == given for s in sources):
            raise click.ClickException(f"This project has no source {given}.")
        return given
    if len(sources) != 1:
        listed = ", ".join(s["id"] for s in sources) or "none"
        raise click.ClickException(f"Name the source; this project has {len(sources)} ({listed}).")
    return sources[0]["id"]


def _where(src: dict) -> str:
    path = f":{src['path']}" if src.get("path") else ""
    return f"{src['repo']}@{src['ref']}{path}"


@click.group()
def project() -> None:
    """Your projects, and the GitHub folders they sync from."""


@project.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List your projects."""
    for p in (_client(ctx).get("/api/projects") or {}).get("projects") or []:
        click.echo(f"{p['id']}  {p['name']}  ({p.get('docCount', 0)} documents)")


@project.group("source")
def source() -> None:
    """A project's GitHub sources."""


@source.command("list")
@click.argument("project_name")
@click.pass_context
def source_list(ctx: click.Context, project_name: str) -> None:
    """List PROJECT_NAME's sources and how their last sync went."""
    client = _client(ctx)
    pid = _project_id(client, project_name)
    sources = _sources(client, pid)
    if not sources:
        click.echo("No sources. Add one with `aiplatform project source add`.")
    for s in sources:
        synced = (s.get("syncedAt") or "never")[:16].replace("T", " ")
        commit = (s.get("syncedCommit") or "")[:7]
        click.echo(f"{s['id']}  {_where(s)} → {s['folderPrefix']}/  {s['status']}  {commit} {synced}")


@source.command("add")
@click.argument("project_name")
@click.argument("repo")
@click.option("--path", default="", help="Folder within the repository; the whole repository when omitted.")
@click.option("--ref", default="main", show_default=True, help="Branch to follow.")
@click.option("--include", multiple=True, help="Glob of files to keep, relative to --path. Repeatable.")
@click.option("--exclude", multiple=True, help="Glob of files to leave out. Repeatable.")
@click.option(
    "--folder", "folder_prefix", default="", help="Library folder to sync into (default: last part of --path)."
)
@click.option("--credential", default="", help="Which of the deployment's GitHub tokens to read with.")
@click.pass_context
def source_add(
    ctx: click.Context,
    project_name: str,
    repo: str,
    path: str,
    ref: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    folder_prefix: str,
    credential: str,
) -> None:
    """Sync PROJECT_NAME from REPO (owner/name). Nothing syncs until `project sync`.

    \b
      aiplatform project source add "Client A" example-org/clients --path client-a
      aiplatform project sync "Client A" --dry-run
    """
    client = _client(ctx)
    pid = _project_id(client, project_name)
    src = client.post(
        f"/api/projects/{pid}/sources",
        json={
            "repo": repo,
            "ref": ref,
            "path": path,
            "include": list(include),
            "exclude": list(exclude),
            "folder_prefix": folder_prefix,
            "credential_ref": credential,
        },
    )
    click.echo(f"Added {src['id']}: {_where(src)} → library folder {src['folderPrefix']}/")
    click.echo("Preview it with `aiplatform project sync --dry-run`.")


@source.command("remove")
@click.argument("project_name")
@click.argument("source_id")
@click.pass_context
def source_remove(ctx: click.Context, project_name: str, source_id: str) -> None:
    """Stop syncing SOURCE_ID. Its documents stay in the project."""
    client = _client(ctx)
    pid = _project_id(client, project_name)
    client.delete(f"/api/projects/{pid}/sources/{source_id}")
    click.echo("Removed. Its documents stay in the library and the project.")


def _print_report(report: dict, *, verbose: bool) -> None:
    c = report.get("counts") or {}
    commit = (report.get("commit") or "")[:7]
    head = "Preview" if report.get("dryRun") else "Sync"
    click.echo(f"{head} at {commit or '?'}: {report.get('status')} ({report.get('seconds', 0)} s)")
    if report.get("error"):
        click.echo(f"  {report['error'].get('message')}")
    if report.get("message"):
        click.echo(f"  {report['message']}")
    order = ("adopted", "added", "updated", "unchanged", "removed", "skipped", "failed", "remaining")
    click.echo("  " + " · ".join(f"{c.get(k, 0)} {k}" for k in order if c.get(k)))
    plan = report.get("plan") or {}
    for key in ("adopt", "add", "update", "remove"):
        items = plan.get(key) or []
        if items and (verbose or key in ("update", "remove")):
            click.echo(f"  {key}: " + ", ".join(items[:50]) + (" …" if len(items) > 50 else ""))
    for label, rows in (
        ("skipped", report.get("skipped") or []),
        ("failed", report.get("failed") or []),
        ("parse failed", report.get("parseFailed") or []),
        ("index failed", report.get("indexFailed") or []),
    ):
        if rows and (verbose or label != "skipped"):
            click.echo(f"  {label}:")
            for r in rows:
                click.echo(f"    {r['path']}: {r.get('reason') or r.get('error') or ''}")
        elif rows:
            click.echo(f"  {len(rows)} {label} (--verbose lists them)")
    if report.get("removalsHeld"):
        click.echo("  held removals: " + ", ".join(report["removalsHeld"][:50]))
        click.echo("  Run again with --confirm-removals once the list looks right.")
    search = report.get("projectSearch")
    if search:
        click.echo(f"  project search: {search.get('indexed')}/{search.get('total')} indexed, {search.get('status')}")


@project.command("sync")
@click.argument("project_name")
@click.argument("source_id", required=False, default="")
@click.option("--dry-run", is_flag=True, help="Show what would change; change nothing.")
@click.option("--confirm-removals", is_flag=True, help="Allow removals the safety limit held back.")
@click.option("--force", is_flag=True, help="Re-check every file even if the branch has not moved.")
@click.option("--verbose", "-v", is_flag=True, help="List every file in the plan and every skip.")
@click.pass_context
def sync_cmd(
    ctx: click.Context,
    project_name: str,
    source_id: str,
    dry_run: bool,
    confirm_removals: bool,
    force: bool,
    verbose: bool,
) -> None:
    """Preview or run a sync of PROJECT_NAME's source (SOURCE_ID if it has several).

    A sync that reaches the server's time limit stops cleanly; this command then
    calls again until it is done."""
    client = _client(ctx)
    pid = _project_id(client, project_name)
    sid = _source_id(client, pid, source_id)
    body = {"dry_run": dry_run, "confirm_removals": confirm_removals, "force": force}
    for _ in range(_MAX_CONTINUATIONS):
        report = client.post(f"/api/projects/{pid}/sources/{sid}/sync", json=body, timeout=_SYNC_TIMEOUT)
        _print_report(report, verbose=verbose)
        if dry_run or not (report.get("counts") or {}).get("remaining"):
            break
        body["force"] = False
        click.echo("Continuing…")
    if report.get("status") == "failed":
        raise SystemExit(1)
