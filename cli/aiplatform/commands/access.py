"""`aitana access check` — effective-access dry-run for a user.

Wired to the v6.9.0 admin API (9.3):
    POST /api/admin/access/check
    body: {"email": "...", "skillId": "...", "toolName": "..."}
    resp: the user's provenanced effective tags (direct + domain-derived),
          plus optional skill/tool permission checks.

Aitana-admin gated server-side. Mirrors the real enforcement path so the
dry-run matches what the user actually gets.
"""

from __future__ import annotations

import json as _json

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def access() -> None:
    """Access-control dry-run helpers."""


@access.command("check")
@click.option("--email", required=True, help="User email to inspect.")
@click.option("--skill", "skill_id", default=None, help="Also check access to this skill id.")
@click.option("--tool", "tool_name", default=None, help="Also check permission for this tool.")
@click.pass_context
def check(ctx: click.Context, email: str, skill_id: str | None, tool_name: str | None) -> None:
    """Show a user's effective tags (with provenance), optionally checking a skill/tool."""
    payload: dict = {"email": email}
    if skill_id:
        payload["skillId"] = skill_id
    if tool_name:
        payload["toolName"] = tool_name
    result = _client(ctx).post("/api/admin/access/check", json=payload)
    click.echo(_json.dumps(result, indent=2))
