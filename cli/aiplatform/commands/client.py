"""`aiplatform client` — admin CRUD for clients/{domain} Firestore config.

Targets backend routes under /api/admin/clients. Requires the caller to
have the `aitana-admin` Firebase group tag (set on the aiplatform admin user).
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def client() -> None:
    """Manage client/tenant domain configs (list/get/set/delete)."""


@client.command("list")
@click.pass_context
def list_clients(ctx: click.Context) -> None:
    """List all registered client domains in a table."""
    result = _client(ctx).get("/api/admin/clients")
    if not result:
        click.echo("No clients registered.")
        return
    header = f"{'DOMAIN':<40} {'DISPLAY NAME':<30} {'DOCUMENTS BUCKET'}"
    click.echo(header)
    click.echo("-" * len(header))
    for entry in result:
        domain = entry.get("domain", "")
        name = entry.get("display_name", "")
        bucket = entry.get("documents_bucket") or ""
        click.echo(f"{domain:<40} {name:<30} {bucket}")


@client.command("get")
@click.argument("domain")
@click.pass_context
def get_client(ctx: click.Context, domain: str) -> None:
    """Show config for a single client domain as JSON."""
    result = _client(ctx).get(f"/api/admin/clients/{domain}")
    click.echo(_json.dumps(result, indent=2))


@client.command("set")
@click.argument("domain")
@click.option(
    "--documents-bucket", "documents_bucket", default=None, help="GCS bucket name for this client's documents."
)
@click.option(
    "--display-name",
    "display_name",
    default=None,
    help="Human-readable client name. Omit to leave unchanged.",
)
@click.option(
    "--enabled-skills",
    "enabled_skills",
    default=None,
    help=(
        "Comma-separated skill slugs visible to this tenant. "
        "Omit to leave unchanged. Pass empty string to clear the filter (= all skills visible)."
    ),
)
@click.option(
    "--default-skill",
    "default_skill",
    default=None,
    help=(
        "Skill slug a signed-in user lands on with no prior chat (v6.5.0 AUTH-LANDING). "
        "Omit to leave unchanged. Pass empty string to clear."
    ),
)
@click.option(
    "--derived-group-tags",
    "derived_group_tags",
    default=None,
    help=(
        "Comma-separated group tags auto-granted to every user from this domain "
        "(unioned with the JWT groupTags claim). Use to grant tagged-skill access "
        "to a customer's whole domain (e.g. 'ONE' for acme-energy.example). "
        "Omit to leave unchanged. Pass empty string to clear."
    ),
)
@click.pass_context
def set_client(
    ctx: click.Context,
    domain: str,
    documents_bucket: str | None,
    display_name: str | None,
    enabled_skills: str | None,
    default_skill: str | None,
    derived_group_tags: str | None,
) -> None:
    """Create or update a client domain config (partial upsert).

    Only flags you pass are sent — omitted fields are left unchanged on the
    server (the upsert merges by set field, not by full document).
    """
    payload: dict = {}
    if display_name is not None:
        payload["display_name"] = display_name
    if default_skill is not None:
        # Empty string clears the landing default.
        payload["default_skill"] = default_skill or None
    if documents_bucket is not None:
        payload["documents_bucket"] = documents_bucket
    if enabled_skills is not None:
        # Empty string clears the filter (all skills visible).
        # Non-empty: split + strip slugs.
        slugs = [s.strip() for s in enabled_skills.split(",") if s.strip()]
        payload["enabled_skills"] = slugs if slugs else None
    if derived_group_tags is not None:
        tags = [t.strip() for t in derived_group_tags.split(",") if t.strip()]
        payload["derived_group_tags"] = tags if tags else None
    result = _client(ctx).put(f"/api/admin/clients/{domain}", json=payload)
    click.echo(_json.dumps(result, indent=2))


@client.command("delete")
@click.argument("domain")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
@click.pass_context
def delete_client(ctx: click.Context, domain: str, yes: bool) -> None:
    """Delete a client domain config."""
    if not yes:
        click.confirm(f"Delete client config for {domain!r}?", abort=True)
    result = _client(ctx).delete(f"/api/admin/clients/{domain}")
    click.echo(_json.dumps(result, indent=2))
