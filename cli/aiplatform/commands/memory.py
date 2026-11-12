"""`aiplatform memory` — see and delete what the assistant remembers about you.

The headless side of Settings → Your memory (docs/design/private/memory-screen.md),
so the journey "remember → list → delete → no longer recalled" can be verified
from a terminal. Acts as the signed-in user (AIPLATFORM_ID_TOKEN), on their own
memory only; `--project` uses one of their projects' memory instead.

Endpoints used:
    GET    /api/me/memories                      — list
    DELETE /api/me/memories/item?ref=…           — delete one
    DELETE /api/me/memories?confirm=forget       — delete all
"""

from __future__ import annotations

import click

from aiplatform.http import AIPlatformClient

_PATH = "/api/me/memories"


def _params(project: str) -> dict[str, str]:
    return {"project_id": project} if project else {}


def _list(client: AIPlatformClient, project: str) -> dict:
    return client.get(_PATH, params=_params(project)) or {}


@click.group()
def memory() -> None:
    """See and delete what the assistant remembers about you."""


_project_option = click.option("--project", default="", help="One of your projects' ids: use its memory instead.")


@memory.command("list")
@_project_option
@click.pass_context
def list_cmd(ctx: click.Context, project: str) -> None:
    """List what is remembered, newest first, numbered for `memory delete`."""
    body = _list(AIPlatformClient(env=ctx.obj["env"]), project)
    if not body.get("available"):
        click.echo("Memory isn't switched on in this environment.")
        return
    memories = body.get("memories") or []
    if not memories:
        click.echo("Nothing remembered yet.")
        return
    for i, m in enumerate(memories, 1):
        when = (m.get("updated_at") or "")[:16].replace("T", " ")
        click.echo(f"{i:>3}. {m['fact']}" + (f"  ({when})" if when else ""))
    if body.get("truncated"):
        click.echo(f"… showing the newest {len(memories)}.")


@memory.command("delete")
@click.argument("number", type=int)
@_project_option
@click.pass_context
def delete_cmd(ctx: click.Context, number: int, project: str) -> None:
    """Delete memory NUMBER, as numbered by `memory list`."""
    client = AIPlatformClient(env=ctx.obj["env"])
    memories = _list(client, project).get("memories") or []
    if not 1 <= number <= len(memories):
        raise click.ClickException(f"There is no memory {number}; `memory list` shows {len(memories)}.")
    chosen = memories[number - 1]
    client.delete(f"{_PATH}/item", params={"ref": chosen["ref"], **_params(project)})
    click.echo(f'Deleted: "{chosen["fact"]}"')


@memory.command("clear")
@_project_option
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@click.pass_context
def clear_cmd(ctx: click.Context, project: str, yes: bool) -> None:
    """Delete everything remembered (or everything in --project)."""
    what = "this project's memory" if project else "everything the assistant remembers about you"
    if not yes:
        click.confirm(f"Delete {what}?", abort=True)
    out = AIPlatformClient(env=ctx.obj["env"]).delete(_PATH, params={"confirm": "forget", **_params(project)}) or {}
    click.echo(f"Deleted {out.get('deleted', 0)} memories.")
    if out.get("failed"):
        click.echo(f"{out['failed']} could not be deleted; run it again.", err=True)
