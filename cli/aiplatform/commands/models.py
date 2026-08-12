"""`aiplatform models` — inspect the backend model registry.

Backed by `GET /api/models` (unauthenticated on the backend, but we still
route through the shared AIPlatformClient so `--env` URL resolution and the
standard auth header path stay consistent with every other command).

`models tiers` resolves the logical tier defaults (lite/smart/...) to their
concrete registry entries so you can see, at a glance, which model each tier
maps to and its provider — the same mapping skills consult via
`skill set --model-tier`.
"""

from __future__ import annotations

import click

from aiplatform.http import AIPlatformClient


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


@click.group()
def models() -> None:
    """Inspect the backend model registry (tiers, list)."""


@models.command("tiers")
@click.pass_context
def tiers(ctx: click.Context) -> None:
    """Show the logical tier -> registry model mapping and platform default."""
    payload = _client(ctx).get("/api/models")
    tier_defaults: dict = payload.get("tier_defaults") or {}
    models_list: list = payload.get("models") or []
    by_id = {m.get("id"): m for m in models_list if isinstance(m, dict)}

    if not tier_defaults:
        click.echo("No tier defaults configured.")
        return

    header = f"{'TIER':<12} {'ID':<24} {'API NAME':<28} {'PROVIDER'}"
    click.echo(header)
    click.echo("-" * len(header))
    for tier, model_id in tier_defaults.items():
        entry = by_id.get(model_id, {})
        api_name = entry.get("api_name") or ""
        provider = entry.get("provider") or ""
        click.echo(f"{tier:<12} {model_id or '':<24} {api_name:<28} {provider}")

    click.echo()
    click.echo(f"platform_default: {payload.get('platform_default') or '—'}")


@models.command("list")
@click.pass_context
def list_models(ctx: click.Context) -> None:
    """List every model in the registry as a table."""
    payload = _client(ctx).get("/api/models")
    models_list: list = payload.get("models") or []
    if not models_list:
        click.echo("No models registered.")
        return

    header = f"{'ID':<24} {'API NAME':<28} {'PROVIDER':<12} {'TIER':<10} {'CONTEXT'}"
    click.echo(header)
    click.echo("-" * len(header))
    for m in models_list:
        if not isinstance(m, dict):
            continue
        ctx_window = m.get("context_window")
        ctx_str = str(ctx_window) if ctx_window is not None else ""
        click.echo(
            f"{m.get('id') or '':<24} {m.get('api_name') or '':<28} "
            f"{m.get('provider') or '':<12} {m.get('tier') or '':<10} {ctx_str}"
        )
