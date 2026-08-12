"""`aitana groups` — grant / revoke / show a user's group tags.

Wired to the v6.9.0 admin API (9.3). Group tags are per-user claims keyed by
EMAIL (the friendly identifier), not uid:

    POST   /api/admin/users/{email}/groups        body: {"tag": "..."}   -> grant
    DELETE /api/admin/users/{email}/groups/{tag}                          -> revoke
    GET    /api/admin/users/{email}                                       -> show

All aitana-admin gated server-side. A grant of a tag not in the registry is
rejected with 422 — the error detail is surfaced (never-silent).
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def groups() -> None:
    """Manage a user's group tags (grant / revoke / show)."""


@groups.command("grant")
@click.option("--email", required=True, help="User email.")
@click.option("--tag", required=True, help="Group tag to grant.")
@click.pass_context
def grant(ctx: click.Context, email: str, tag: str) -> None:
    """Grant a group tag to a user (idempotent)."""
    result = _client(ctx).post(f"/api/admin/users/{email}/groups", json={"tag": tag})
    click.echo(_json.dumps(result, indent=2))


@groups.command("revoke")
@click.option("--email", required=True, help="User email.")
@click.option("--tag", required=True, help="Group tag to revoke.")
@click.pass_context
def revoke(ctx: click.Context, email: str, tag: str) -> None:
    """Revoke a group tag from a user (idempotent)."""
    result = _client(ctx).delete(f"/api/admin/users/{email}/groups/{tag}")
    click.echo(_json.dumps(result, indent=2) if result else f"Revoked {tag!r} from {email}")


@groups.command("show")
@click.option("--email", required=True, help="User email.")
@click.pass_context
def show(ctx: click.Context, email: str) -> None:
    """Show a user's per-user group tags."""
    result = _client(ctx).get(f"/api/admin/users/{email}")
    click.echo(_json.dumps(result, indent=2))
