"""`aiplatform browse` — drive the isolated browser service from a terminal.

Replaces the "mint a Firebase token, hand-write a JSON-RPC envelope, curl it"
loop with typed commands.

`probe` earns its place ahead of the others: the likeliest production failure
for this service is an IAM or ingress misconfiguration, and the answer to
"is it deployed, reachable, and running as the identity I think?" should be one
command rather than three console tabs.

Reuses ``toolbox.mcp_rpc`` rather than growing a third MCP client — that helper
already carries the dual Accept header the protocol requires (a plain
``application/json`` Accept gets a 406 from some MCP servers).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import click
import httpx

from aiplatform.commands.toolbox import mcp_rpc

DEFAULT_LOCAL_URL = "http://127.0.0.1:8080"


def _service_url(url: str | None) -> str:
    """Resolve the browser service base URL."""
    resolved = (url or os.environ.get("BROWSER_SERVICE_URL") or DEFAULT_LOCAL_URL).rstrip("/")
    if resolved.endswith("/mcp"):
        resolved = resolved[: -len("/mcp")]
    return resolved


def _id_token(audience: str) -> str | None:
    """Mint a Google ID token for an IAM-gated service, via gcloud.

    Returns None for a local http:// target, which needs no token. Uses gcloud
    rather than google-auth so the CLI works with whatever the operator is
    already logged in as, which is the usual source of confusion here.
    """
    if not audience.startswith("https://"):
        return None
    try:
        out = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise click.ClickException("gcloud is not on PATH, so no identity token can be minted") from exc
    if out.returncode != 0:
        raise click.ClickException(
            f"could not mint an identity token for {audience}.\n"
            f"gcloud said: {out.stderr.strip()[:300]}\n"
            "If the token expired, run: gcloud auth login"
        )
    return out.stdout.strip()


def _headers(audience: str, user: str, session: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Aitana-User": user,
        "X-Aitana-Session": session,
    }
    token = _id_token(audience)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _call(base: str, tool: str, arguments: dict, user: str, session: str) -> dict:
    """One MCP tools/call against the browser gateway."""
    headers = _headers(base, user, session)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    try:
        resp = httpx.post(f"{base}/mcp", headers=headers, json=body, timeout=120)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"cannot reach {base}: {exc}") from exc

    if resp.status_code == 403:
        raise click.ClickException(
            f"403 from {base}.\n"
            "Either your account may not invoke the service (IAM: roles/run.invoker),\n"
            "or ingress is internal-only and you are calling from outside the VPC.\n"
            "Run `aiplatform browse probe` to see which."
        )
    if resp.status_code != 200:
        raise click.ClickException(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _text(result: dict) -> str:
    content = (result.get("result") or {}).get("content") or []
    return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")


@click.group()
def browse() -> None:
    """Drive the isolated browser service."""


@browse.command("probe")
@click.option("--url", default=None, help="Browser service base URL (default: $BROWSER_SERVICE_URL).")
def probe(url: str | None) -> None:
    """Health, identity, and reachability of the browser service.

    Prints the service account it is actually running as — the isolation claim
    of the whole design is that this identity holds no permissions, and this is
    how you check that without opening the console.
    """
    base = _service_url(url)
    click.echo(f"probing {base}")

    try:
        headers = {}
        token = _id_token(base)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.get(f"{base}/status", headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"cannot reach {base}: {exc}") from exc

    if resp.status_code == 403:
        raise click.ClickException(
            "403 — reachable but not authorised.\n"
            "Grant roles/run.invoker, or check ingress is not internal-only for your network."
        )
    if resp.status_code != 200:
        raise click.ClickException(f"HTTP {resp.status_code}: {resp.text[:300]}")

    info = resp.json()
    click.echo(f"  status          {info.get('status')}")
    click.echo(f"  service         {info.get('service')} ({info.get('revision')})")
    click.echo(f"  project         {info.get('project')}")
    click.echo(f"  service account {info.get('service_account')}")
    click.echo(f"  commit          {info.get('commit')}")
    click.echo(f"  live sessions   {info.get('sessions')}")


@browse.command("url")
@click.argument("target")
@click.option("--url", "service", default=None, help="Browser service base URL.")
@click.option("--user", default=None, help="Act as this user id (default: your gcloud account).")
@click.option("--session", default="cli", help="Session id, for reusing a browser between calls.")
@click.option("--json", "as_json", is_flag=True, help="Emit the raw result as JSON.")
def browse_url(target: str, service: str | None, user: str | None, session: str, as_json: bool) -> None:
    """Open TARGET in a real browser and print it as Markdown."""
    base = _service_url(service)
    user = user or _default_user()

    _call(base, "browser_navigate", {"url": target}, user, session)
    result = _call(base, "browser_markdown", {}, user, session)

    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(_text(result))


@browse.command("shot")
@click.argument("target")
@click.option("-o", "--out", type=click.Path(path_type=Path), default=Path("screenshot.png"))
@click.option("--url", "service", default=None, help="Browser service base URL.")
@click.option("--user", default=None)
@click.option("--session", default="cli")
def shot(target: str, out: Path, service: str | None, user: str | None, session: str) -> None:
    """Screenshot TARGET to a local PNG."""
    base = _service_url(service)
    user = user or _default_user()

    _call(base, "browser_navigate", {"url": target}, user, session)
    result = _call(base, "browser_screenshot", {}, user, session)

    content = (result.get("result") or {}).get("content") or []
    for part in content:
        if part.get("type") == "image":
            out.write_bytes(base64.b64decode(part["data"]))
            click.echo(f"wrote {out} ({out.stat().st_size} bytes)")
            return
    raise click.ClickException(f"no image in the response: {_text(result)[:300]}")


@browse.group("session")
def session_group() -> None:
    """Inspect and close live browser sessions."""


@session_group.command("list")
@click.option("--url", "service", default=None)
def session_list(service: str | None) -> None:
    """List live browser sessions and their memory use."""
    base = _service_url(service)
    headers = {}
    token = _id_token(base)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(f"{base}/sessions", headers=headers, timeout=30)
    if resp.status_code != 200:
        raise click.ClickException(f"HTTP {resp.status_code}: {resp.text[:300]}")

    sessions = resp.json().get("sessions") or []
    if not sessions:
        click.echo("no live sessions")
        return
    click.echo(f"{'user':<32} {'session':<20} {'age s':>7} {'idle s':>7} {'peak MB':>8}")
    for s in sessions:
        click.echo(
            f"{s['user_id']:<32} {s['session_id']:<20} "
            f"{s['age_seconds']:>7.0f} {s['idle_seconds']:>7.0f} {s['peak_rss_mb']:>8.0f}"
        )


@session_group.command("close")
@click.argument("session_id")
@click.option("--url", "service", default=None)
@click.option("--user", default=None)
def session_close(session_id: str, service: str | None, user: str | None) -> None:
    """Close a browser session by id."""
    base = _service_url(service)
    user = user or _default_user()
    result = _call(base, "browser_close", {}, user, session_id)
    click.echo(_text(result) or "closed")


@browse.command("register")
@click.option("--project", required=True, help="Firestore project, e.g. example-platform-dev.")
@click.option("--url", "service", default=None, help="Browser service base URL (default: $BROWSER_SERVICE_URL).")
@click.option("--dry-run", is_flag=True, help="Print the row, write nothing.")
def register(project: str, service: str | None, dry_run: bool) -> None:
    """Register the browser in mcp_servers/ so skills can declare it.

    Until this row exists, a SKILL.md carrying `mcp.servers: ["browser"]` fails
    strict resolution at agent build (G42) — loudly, which is the intended
    behaviour, but it means the browser is invisible to skill authors.

    `mcp_servers` rows are operator-created on purpose: platform_seed VALIDATES
    that declared servers resolve and fails the build if they do not, but it
    never invents them, because the URL is per-environment.
    """
    base = _service_url(service)
    if not base.startswith("https://"):
        raise click.ClickException(
            f"refusing to register a non-https browser URL ({base}). Pass --url with the deployed service URL."
        )

    row = {
        "name": "Browser — isolated Obscura browsing service",
        "url": f"{base}/mcp",
        "transport": "http",
        # Mints a per-call ID token for the Cloud Run edge AND carries the
        # calling user in X-Aitana-User. Without this the toolset dials an
        # IAM-gated service with no credential and 403s.
        "auth": "google_id_token",
        # The browser service holds zero project roles and cannot read the
        # allowlist itself, so the backend gates before dialling.
        "gate": "browser_allowlist",
        "operated_by": "aitana",
        "source_repo": "https://github.com/sunholo-data/obscura",
        "tags": ["browser", "web", "scraping"],
        "headers": {},
    }

    click.echo(json.dumps(row, indent=2))
    if dry_run:
        click.echo("\nDRY RUN — nothing written.")
        return

    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise click.ClickException("google-cloud-firestore is not installed in this interpreter") from exc

    client = firestore.Client(project=project)
    client.collection("mcp_servers").document("browser").set(row, merge=True)
    click.echo(f"\nwrote mcp_servers/browser in {project}")
    click.echo('Skills can now declare:  mcp.servers: ["browser"]')


def _principals(user: str | None, domain: str | None) -> list[str]:
    if not user and not domain:
        raise click.ClickException("pass --user and/or --domain")
    return [p for p in (user, domain) if p]


@browse.command("grant")
@click.option("--project", required=True, help="Firestore project, e.g. example-platform-dev.")
@click.option("--user", default=None, help="Grant to one user email.")
@click.option("--domain", default=None, help="Grant to a whole domain, e.g. example.com.")
@click.option("--dry-run", is_flag=True, help="Print the changes, write nothing.")
def grant(project: str, user: str | None, domain: str | None, dry_run: bool) -> None:
    """Allow a user or domain to use the browser.

    TWO independent gates have to agree, which is deliberate — one is about the
    capability, one about the service:

      1. tool_permissions/  — may this principal invoke the `browse` tool.
         FIRST MATCH WINS: a user doc short-circuits the domain and wildcard
         docs entirely, so a grant means writing the right level.
      2. admin_config/browser_allowlist — may they reach the browser SERVICE.
         Checked by the backend before dialling, because the service holds zero
         project roles and cannot read this itself.

    Skill visibility is a third, separate plane (accessControl: owner /
    specific / domain / tagged) set on the skill, not here.
    """
    client = _firestore(project)
    targets = _principals(user, domain)
    changes = []

    perms = client.collection("tool_permissions")
    for t in targets:
        snap = perms.document(t).get()
        data = (snap.to_dict() or {}) if snap.exists else {}
        tools = data.get("tools") or []
        denied = [d for d in (data.get("denied") or []) if d != "browse"]
        if "*" not in tools and "browse" not in tools:
            tools = sorted(set(tools) | {"browse"})
        changes.append((perms.document(t), {"tools": tools, "denied": denied}, f"tool_permissions/{t}: browse allowed"))

    allow = client.collection("admin_config").document("browser_allowlist")
    snap = allow.get()
    cfg = (snap.to_dict() or {}) if snap.exists else {}
    users = sorted(set(cfg.get("users") or []) | ({user} if user else set()))
    domains = sorted(set(cfg.get("domains") or []) | ({domain} if domain else set()))
    changes.append((allow, {"users": users, "domains": domains}, f"browser_allowlist: users={users} domains={domains}"))

    _apply(changes, dry_run, project)


@browse.command("deny")
@click.option("--project", required=True, help="Firestore project.")
@click.option("--user", default=None, help="Deny one user email.")
@click.option("--domain", default=None, help="Deny a whole domain — withholds the browser from that tenant only.")
@click.option("--dry-run", is_flag=True, help="Print the changes, write nothing.")
def deny(project: str, user: str | None, domain: str | None, dry_run: bool) -> None:
    """Deny the browser to a user or domain, leaving their other tools alone.

    A domain doc carrying tools: ["*"] grants every tool including this one, so
    withholding the browser from a tenant means an explicit entry in `denied` —
    NOT narrowing `tools`, which would take away everything else they have.
    """
    client = _firestore(project)
    targets = _principals(user, domain)
    changes = []

    perms = client.collection("tool_permissions")
    for t in targets:
        snap = perms.document(t).get()
        if not snap.exists:
            click.echo(f"  tool_permissions/{t}: no doc — the wildcard already withholds `browse`")
            continue
        data = snap.to_dict() or {}
        denied = sorted(set(data.get("denied") or []) | {"browse"})
        changes.append((perms.document(t), {"denied": denied}, f"tool_permissions/{t}: denied={denied}"))

    allow = client.collection("admin_config").document("browser_allowlist")
    snap = allow.get()
    if snap.exists:
        cfg = snap.to_dict() or {}
        users = [u for u in (cfg.get("users") or []) if u != user]
        domains = [d for d in (cfg.get("domains") or []) if d != domain]
        changes.append(
            (allow, {"users": users, "domains": domains}, f"browser_allowlist: users={users} domains={domains}")
        )

    _apply(changes, dry_run, project)


def _firestore(project: str):
    try:
        from google.cloud import firestore
    except ImportError as exc:
        raise click.ClickException("google-cloud-firestore is not installed in this interpreter") from exc
    return firestore.Client(project=project)


def _apply(changes, dry_run: bool, project: str) -> None:
    for _, _, description in changes:
        click.echo(f"  {description}")
    if dry_run:
        click.echo("\nDRY RUN — nothing written.")
        return
    for ref, payload, _ in changes:
        ref.set(payload, merge=True)
    click.echo(f"\ndone in {project}. Takes effect within 60s (both gates cache for that long).")


@browse.command("tools")
@click.option("--url", "service", default=None)
@click.option("--user", default=None)
def tools(service: str | None, user: str | None) -> None:
    """List the browser tools the model is allowed to call.

    Useful for confirming the credential tools really are withheld: the
    ``browser_storage_state`` family and ``browser_snapshot`` must NOT appear.
    """
    base = _service_url(service)
    user = user or _default_user()
    headers = _headers(base, user, "cli")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = httpx.post(f"{base}/mcp", headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        raise click.ClickException(f"HTTP {resp.status_code}: {resp.text[:300]}")

    listed = (resp.json().get("result") or {}).get("tools") or []
    if not listed:
        click.echo("no tools — is your account on the browser allowlist?")
        return
    for tool in sorted(listed, key=lambda t: t["name"]):
        click.echo(f"  {tool['name']}")
    click.echo(f"\n{len(listed)} tools exposed")

    withheld = {"browser_storage_state", "browser_set_storage_state", "browser_snapshot"}
    leaked = withheld & {t["name"] for t in listed}
    if leaked:
        raise click.ClickException(f"SECURITY: withheld tools are being exposed: {sorted(leaked)}")


def _default_user() -> str:
    """The gcloud account, so `--user` is rarely needed interactively."""
    try:
        out = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True, timeout=15)
        account = out.stdout.strip().splitlines()[-1].strip() if out.returncode == 0 else ""
        if account and "@" in account:
            return account
    except Exception:
        pass
    return "cli-user"


__all__ = ["browse", "mcp_rpc"]
