"""`aiplatform approvals` — decide human approvals from a terminal.

Some tools prepare a write that only a person may release (e.g. Severa hours).
These commands show what is waiting and let you approve or cancel it, showing
the exact server-written review first and sending back the digest of what you
saw (docs/design/v6.41.0/human-approvals.md).

Auth: set AIPLATFORM_APPROVAL_TOKEN to a personal token minted with "Can
approve actions" (Settings → MCP). It is used only by these commands. Without
it they use your normal sign-in; machine and delegate tokens can list and read
but never decide. High-risk requests always need a fresh sign-in in the web
app, so they can't be approved here.

Endpoints used:
    GET  /api/approvals                    — what waits for you
    GET  /api/approvals/{id}               — one request, with its review
    POST /api/approvals/{id}/decision      — {decision, digest}
"""

from __future__ import annotations

import os

import click

from aiplatform.http import AIPlatformClient

_PATH = "/api/approvals"


def _client(ctx: click.Context) -> AIPlatformClient:
    token = os.environ.get("AIPLATFORM_APPROVAL_TOKEN", "").strip() or None
    return AIPlatformClient(env=ctx.obj["env"], token=token)


def _print_review(request: dict) -> None:
    click.echo(click.style(request["title"], bold=True))
    click.echo(request["review"])
    click.echo(f"\nid {request['id']} · digest {request['digest'][:12]}")


def _decide(client: AIPlatformClient, request: dict, decision: str) -> None:
    receipt = client.post(f"{_PATH}/{request['id']}/decision", json={"decision": decision, "digest": request["digest"]})
    click.echo(receipt.get("message") or receipt.get("state") or "Done.")


@click.group()
def approvals() -> None:
    """See and decide actions waiting for your approval."""


@approvals.command("list")
@click.option("--session", default="", help="Only requests prepared in this conversation.")
@click.pass_context
def list_cmd(ctx: click.Context, session: str) -> None:
    """List what is waiting for your approval."""
    body = _client(ctx).get(_PATH, params={"session": session} if session else None) or {}
    pending = body.get("approvals") or []
    if not pending:
        click.echo("Nothing is waiting for your approval.")
        return
    for request in pending:
        note = "  (needs a fresh sign-in)" if request["risk"] == "high" else ""
        click.echo(f"{request['id']}  {request['title']}{note}")
    click.echo("\nApprove one with: aiplatform approvals approve <id>")


@approvals.command("show")
@click.argument("approval_id")
@click.pass_context
def show_cmd(ctx: click.Context, approval_id: str) -> None:
    """Show one request's full review."""
    _print_review(_client(ctx).get(f"{_PATH}/{approval_id}"))


@approvals.command("approve")
@click.argument("approval_id")
@click.pass_context
def approve_cmd(ctx: click.Context, approval_id: str) -> None:
    """Show the review, ask, then approve exactly what you saw."""
    client = _client(ctx)
    request = client.get(f"{_PATH}/{approval_id}")
    _print_review(request)
    if request["risk"] == "high":
        raise click.ClickException("This action needs a fresh sign-in. Approve it in the web app.")
    if not click.confirm("\nApprove this?", default=False):
        click.echo("Not approved. Nothing was sent.")
        return
    _decide(client, request, "approve")


@approvals.command("cancel")
@click.argument("approval_id")
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@click.pass_context
def cancel_cmd(ctx: click.Context, approval_id: str, yes: bool) -> None:
    """Discard a request without doing it."""
    client = _client(ctx)
    request = client.get(f"{_PATH}/{approval_id}")
    if not yes:
        _print_review(request)
        if not click.confirm("\nDiscard this without doing it?", default=False):
            click.echo("Kept. Nothing was sent.")
            return
    _decide(client, request, "cancel")
