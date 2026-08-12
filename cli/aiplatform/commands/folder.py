"""`aitana folder` — list / create subcommands.

Targets /api/buckets/{bucket_id}/folders (see backend/buckets/routes.py).
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def folder() -> None:
    """Manage folders inside a bucket (list/create)."""


@folder.command("list")
@click.option("--bucket", "bucket_id", required=True, help="Parent bucket ID.")
@click.option("--limit", type=int, default=50, show_default=True)
@click.pass_context
def list_folders(ctx: click.Context, bucket_id: str, limit: int) -> None:
    """List folders inside a bucket (visible to caller)."""
    result = _client(ctx).get(f"/api/buckets/{bucket_id}/folders", params={"limit": limit})
    click.echo(_json.dumps(result, indent=2))


@folder.command("create")
@click.option("--bucket", "bucket_id", required=True, help="Parent bucket ID.")
@click.option("--path", required=True, help="Folder path (e.g. 'reports/2026').")
@click.option("--display-name", "display_name", required=True)
@click.option(
    "--access-type",
    "access_type",
    type=click.Choice(["public", "private", "domain", "specific", "tagged", "inherit"]),
    default="inherit",
    show_default=True,
    help="'inherit' skips accessControl (folder inherits parent bucket's).",
)
@click.option("--domain", default=None)
@click.option("--email", "emails", multiple=True)
@click.option("--tag", "acl_tags", multiple=True)
@click.option("--folder-tag", "tags", multiple=True, help="Free-form folder tag (repeatable).")
@click.pass_context
def create_folder(
    ctx: click.Context,
    bucket_id: str,
    path: str,
    display_name: str,
    access_type: str,
    domain: str | None,
    emails: tuple[str, ...],
    acl_tags: tuple[str, ...],
    tags: tuple[str, ...],
) -> None:
    """Create a folder. Omit --access-type (or pass 'inherit') to inherit parent bucket ACL."""
    payload: dict = {
        "path": path,
        "displayName": display_name,
        "tags": list(tags),
    }
    if access_type != "inherit":
        access_control: dict = {"type": access_type}
        if access_type == "domain":
            if not domain:
                raise click.UsageError("--domain is required when --access-type=domain")
            access_control["domain"] = domain
        elif access_type == "specific":
            if not emails:
                raise click.UsageError("--email (one or more) is required when --access-type=specific")
            access_control["emails"] = list(emails)
        elif access_type == "tagged":
            if not acl_tags:
                raise click.UsageError("--tag (one or more) is required when --access-type=tagged")
            access_control["tags"] = list(acl_tags)
        payload["accessControl"] = access_control

    result = _client(ctx).post(f"/api/buckets/{bucket_id}/folders", json=payload)
    click.echo(_json.dumps(result, indent=2))
