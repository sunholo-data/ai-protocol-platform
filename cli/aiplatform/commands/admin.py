"""`aiplatform admin` — inspect your own admin authority.

Backed by `GET /api/admin/whoami` (v6.16.0 ADMIN-SCOPE M1), the role probe the
admin console gates on.

This is the one-command answer to "is my tenant-admin claim actually working?",
which is otherwise a genuinely annoying question: group tags live in a signed
JWT claim, they only take effect on the next token refresh, and they are NOT
promoted between environments with code (see docs/ops/env-config-parity.md), so
"it works in dev" tells you nothing about test. Before this you had to mint a
token by hand and curl an admin endpoint, then infer your role from whether you
got a 403 — the same conflation of "not an admin" with "something is broken"
that the whoami endpoint exists to end.

Note: the effective-access dry-run already ships as `aiplatform access check`
(v6.9.0). It is not duplicated here.
"""

from __future__ import annotations

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def admin() -> None:
    """Admin-scope helpers."""


@admin.command("whoami")
@click.pass_context
def whoami(ctx: click.Context) -> None:
    """Show your resolved admin scope (platform / tenant / none)."""
    payload = _client(ctx).get("/api/admin/whoami")
    scope = str(payload.get("scope") or "none")
    domains = payload.get("domains") or []
    email = str(payload.get("email") or "")

    if email:
        click.echo(f"Signed in as: {email}")

    if scope == "platform":
        click.echo("Admin scope:   platform (aitana-admin) — every tenant")
    elif scope == "tenant":
        click.echo(f"Admin scope:   tenant — {', '.join(str(d) for d in domains)}")
    else:
        # Never a bare "none": say what would change the answer. A missing tag
        # and a stale token look identical from here, so name both.
        click.echo("Admin scope:   none — you are not an admin in this environment")
        click.echo(
            "\nIf you expect access: check the aitana-admin / tenant-admin:<domain> group tag\n"
            "is granted IN THIS ENVIRONMENT (claims don't promote with code), then sign out\n"
            "and back in — claims only refresh on a new token."
        )
