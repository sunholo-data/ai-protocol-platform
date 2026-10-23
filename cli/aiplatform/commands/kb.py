"""`aiplatform kb` — knowledge bases: list / status / sync / search.

Targets /api/knowledge-bases (backend/private/knowledge/routes.py). A
deployment without the private knowledge module answers 404 — reported as
"no knowledge bases on this deployment", not a stack trace.
"""

from __future__ import annotations

import json as _json
import time

import click

from aiplatform.http import AIPlatformClient, APIError

_RUNNING = ("queued", "listing", "indexing", "removing")


def _client(ctx: click.Context) -> AIPlatformClient:
    return AIPlatformClient(env=ctx.obj["env"])


def _call(fn, path: str, **kwargs):
    try:
        return fn(path, **kwargs)
    except APIError as exc:
        if " returned 404" in str(exc) and path.rstrip("/") == "/api/knowledge-bases":
            raise click.ClickException("This deployment has no knowledge bases (the API answered 404).") from exc
        raise


def _summary_line(summary: dict) -> str:
    if not summary:
        return "never synced"
    parts = [summary.get("state", "?")]
    for key in ("indexed", "unchanged", "failed", "orphansRemoved"):
        if summary.get(key):
            parts.append(f"{key} {summary[key]}")
    if summary.get("total"):
        parts.append(f"{summary.get('done', 0)}/{summary['total']}")
    return ", ".join(parts)


@click.group()
def kb() -> None:
    """Knowledge bases: list, status, sync, search."""


@kb.command("list")
@click.option("--json", "as_json", is_flag=True, help="Raw JSON.")
@click.pass_context
def list_kbs(ctx: click.Context, as_json: bool) -> None:
    """Knowledge bases you can read."""
    rows = _call(_client(ctx).get, "/api/knowledge-bases")
    if as_json:
        click.echo(_json.dumps(rows, indent=2))
        return
    if not rows:
        click.echo("No knowledge bases you can read.")
        return
    for r in rows:
        sources = ", ".join(s.get("name") or s.get("type", "?") for s in r.get("sources") or [])
        click.echo(
            f"{r['kbId']:<28} {r.get('name', '')!s:<28} {r.get('documentCount', 0):>6} docs  "
            f"searchable by {r.get('audience', '?')}  [{sources}]"
        )


@kb.command("status")
@click.argument("kb_ref")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def status(ctx: click.Context, kb_ref: str, as_json: bool) -> None:
    """Document count, last sync and failed files of KB_REF (id or name)."""
    st = _client(ctx).get(f"/api/knowledge-bases/{kb_ref}/status")
    if as_json:
        click.echo(_json.dumps(st, indent=2))
        return
    click.echo(f"{st['name']} ({st['kbId']}): {st['documentCount']} documents")
    summary = st.get("lastSyncSummary") or {}
    click.echo(f"last sync: {_summary_line(summary)}")
    for label, src in (summary.get("sources") or {}).items():
        click.echo(f"  {label}: {src.get('error') or str(src.get('files', '?')) + ' files'}")
    failed = st.get("failed") or []
    if failed:
        click.echo(f"{len(failed)} failed:")
        for f in failed[:20]:
            click.echo(f"  {f.get('path') or f.get('title')}: {f.get('error')}")
        if len(failed) > 20:
            click.echo(f"  … and {len(failed) - 20} more (--json for all)")


@kb.command("sync")
@click.argument("kb_ref")
@click.option("--wait/--no-wait", default=True, show_default=True, help="Follow progress until it finishes.")
@click.option("--timeout", type=int, default=3600, show_default=True, help="Seconds to wait.")
@click.pass_context
def sync(ctx: click.Context, kb_ref: str, wait: bool, timeout: int) -> None:
    """Re-sync KB_REF from its sources (adds new, re-indexes changed, drops removed)."""
    client = _client(ctx)
    r = client.post(f"/api/knowledge-bases/{kb_ref}/sync")
    click.echo(r.get("message") or f"{r['kbId']}: {r['state']}")
    if not wait:
        return
    deadline, last = time.time() + timeout, ""
    while time.time() < deadline:
        time.sleep(5)
        summary = client.get(f"/api/knowledge-bases/{kb_ref}/status").get("lastSyncSummary") or {}
        line = _summary_line(summary)
        if line != last:
            click.echo(f"  {line}")
            last = line
        if summary.get("state") not in _RUNNING:
            errors = [f"{k}: {v['error']}" for k, v in (summary.get("sources") or {}).items() if v.get("error")]
            for e in errors:
                click.echo(f"  source problem — {e}", err=True)
            if errors:
                raise SystemExit(1)
            return
    raise click.ClickException(f"still syncing after {timeout}s — check `aiplatform kb status {kb_ref}`")


@kb.command("search")
@click.argument("kb_ref")
@click.argument("query")
@click.option("--filter", "filters", multiple=True, help="field=value (repeatable), e.g. status=implemented.")
@click.option("--sort", default="relevance", show_default=True, help="relevance | newest | oldest")
@click.option("--limit", type=int, default=8, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def search(
    ctx: click.Context, kb_ref: str, query: str, filters: tuple[str, ...], sort: str, limit: int, as_json: bool
) -> None:
    """Search KB_REF for QUERY — the same search a bound skill runs."""
    chosen: dict[str, str] = {}
    for f in filters:
        key, sep, value = f.partition("=")
        if not sep:
            raise click.UsageError(f"--filter wants field=value, got {f!r}")
        chosen[key.strip()] = value.strip()
    body = {"query": query, "filters": chosen, "sort": sort, "pageSize": limit}
    r = _client(ctx).post(f"/api/knowledge-bases/{kb_ref}/search", json=body)
    if as_json:
        click.echo(_json.dumps(r, indent=2))
        return
    results = r.get("results") or []
    if not results:
        click.echo(f"No matches in {r.get('knowledgeBase', kb_ref)}.")
        return
    for i, hit in enumerate(results, 1):
        meta = hit.get("metadata") or {}
        where = meta.get("path") or hit.get("uri") or ""
        click.echo(f"{i}. {hit.get('title') or where}  [{where}]")
        snippet = " ".join((hit.get("snippet") or "").split())
        if snippet:
            click.echo(f"   {snippet[:240]}")
