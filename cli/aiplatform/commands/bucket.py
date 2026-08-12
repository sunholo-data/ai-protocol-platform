"""`aitana bucket` — list / show / create / grant / revoke subcommands.

Targets backend routes under /api/buckets (see backend/buckets/routes.py).
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def bucket() -> None:
    """Manage buckets (list/show/create/grant/revoke)."""


@bucket.command("list")
@click.option("--owner-id", "owner_id", default=None, help="Filter by ownerId.")
@click.option("--tag", default=None, help="Filter by tag.")
@click.option("--access-type", "access_type", default=None, help="Filter by accessControl.type.")
@click.option("--limit", type=int, default=50, show_default=True)
@click.pass_context
def list_buckets(
    ctx: click.Context, owner_id: str | None, tag: str | None, access_type: str | None, limit: int
) -> None:
    """List buckets visible to the caller."""
    params = {"limit": limit}
    if owner_id:
        params["ownerId"] = owner_id
    if tag:
        params["tag"] = tag
    if access_type:
        params["accessType"] = access_type
    result = _client(ctx).get("/api/buckets", params=params)
    click.echo(_json.dumps(result, indent=2))


@bucket.command("show")
@click.argument("bucket_id")
@click.pass_context
def show_bucket(ctx: click.Context, bucket_id: str) -> None:
    """Show a single bucket by ID."""
    result = _client(ctx).get(f"/api/buckets/{bucket_id}")
    click.echo(_json.dumps(result, indent=2))


@bucket.command("create")
@click.option("--display-name", "display_name", required=True, help="Human-readable bucket name.")
@click.option("--gcs-bucket", "gcs_bucket", required=True, help="Backing GCS bucket name.")
@click.option("--region", default="europe-west1", show_default=True)
@click.option(
    "--access-type",
    "access_type",
    type=click.Choice(["public", "private", "domain", "specific", "tagged"]),
    default="private",
    show_default=True,
)
@click.option("--domain", default=None, help="Required when --access-type=domain.")
@click.option("--email", "emails", multiple=True, help="Email (repeatable) for access-type=specific.")
@click.option("--tag", "acl_tags", multiple=True, help="Tag (repeatable) for access-type=tagged.")
@click.option("--bucket-tag", "tags", multiple=True, help="Free-form bucket tag (repeatable).")
@click.pass_context
def create_bucket(
    ctx: click.Context,
    display_name: str,
    gcs_bucket: str,
    region: str,
    access_type: str,
    domain: str | None,
    emails: tuple[str, ...],
    acl_tags: tuple[str, ...],
    tags: tuple[str, ...],
) -> None:
    """Create a new bucket. ownerId is derived from your JWT server-side."""
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

    payload = {
        "displayName": display_name,
        "gcsBucket": gcs_bucket,
        "region": region,
        "accessControl": access_control,
        "tags": list(tags),
    }
    result = _client(ctx).post("/api/buckets", json=payload)
    click.echo(_json.dumps(result, indent=2))


def _mutate_emails(ctx: click.Context, bucket_id: str, email: str, *, add: bool) -> None:
    """GET the bucket, mutate accessControl.emails, PUT it back."""
    client = _client(ctx)
    current = client.get(f"/api/buckets/{bucket_id}")
    ac = dict(current.get("accessControl") or {})
    if ac.get("type") != "specific":
        raise click.ClickException(
            f"Bucket {bucket_id} has accessControl.type={ac.get('type')!r}, not 'specific'. "
            "grant/revoke only operate on specific-email ACLs."
        )
    emails = list(ac.get("emails") or [])
    if add:
        if email not in emails:
            emails.append(email)
    else:
        emails = [e for e in emails if e != email]
    ac["emails"] = emails
    updated = client.put(f"/api/buckets/{bucket_id}", json={"accessControl": ac})
    click.echo(_json.dumps(updated, indent=2))


@bucket.command("grant")
@click.argument("bucket_id")
@click.option("--email", required=True, help="Email to add to accessControl.emails.")
@click.pass_context
def grant(ctx: click.Context, bucket_id: str, email: str) -> None:
    """Grant a user access to a 'specific' bucket by appending their email."""
    _mutate_emails(ctx, bucket_id, email, add=True)


@bucket.command("revoke")
@click.argument("bucket_id")
@click.option("--email", required=True, help="Email to remove from accessControl.emails.")
@click.pass_context
def revoke(ctx: click.Context, bucket_id: str, email: str) -> None:
    """Revoke a user's access to a 'specific' bucket."""
    _mutate_emails(ctx, bucket_id, email, add=False)
