"""`aiplatform user` — start from the PERSON, not the uid.

Every other diagnostic ladder in this CLI (`logs client --uid`, `logs -g
<uid>`, `groups show --email`) assumes you already hold a uid or an exact
email. Support tickets do not arrive that way — they arrive as "Ada can't
see the admin panel" — and turning a name into a uid, working out WHICH env
they were on, and reading whether a just-granted claim has reached their
browser used to take three ad-hoc Firebase Admin scripts and a hand-built
Cloud Logging filter (2026-09-14). This group makes each of those one call:

    aiplatform user find arias                       # name/email needle, ALL envs
    aiplatform --env prod user show msc@example.com  # record + roles + propagation
    aiplatform --env prod user activity msc@example.com --since 2h
    aiplatform --env prod user refresh-claims msc@example.com

Reads go straight to the Identity Toolkit / Firestore REST APIs with your
gcloud application-default credentials — NO backend admin token needed, the
same authority `logs` already relies on. Only ``refresh-claims`` mutates, and
it goes through the audited backend route so the admin trail is kept.

``find`` is a FILTERED search that prints matches only. Never turn it into a
dump of every account: an org's full user list is customer data, and a needle
is all a ticket ever needs.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import re
import shutil
import subprocess
from typing import Any

import click
import httpx

from aiplatform.config import load_config
from aiplatform.http import AIPlatformClient

_CONFIG: dict[str, Any] = load_config()
_LOG_CFG: dict[str, Any] = _CONFIG.get("logging", {})

_IDTK = "https://identitytoolkit.googleapis.com/v1/projects/{proj}/accounts:{verb}"
_FS_DOC = "https://firestore.googleapis.com/v1/projects/{proj}/databases/(default)/documents/{path}"
_FS_QUERY = "https://firestore.googleapis.com/v1/projects/{proj}/databases/(default)/documents:runQuery"

# Mirrors backend/auth/admin_roles.py. Kept inline (the CLI must not import the
# backend); if that module changes shape, change this too.
_PLATFORM_ADMIN_TAG = "aitana-admin"
_TENANT_ADMIN_PREFIX = "tenant-admin:"
_SKILL_ADMIN_TAGS = {_PLATFORM_ADMIN_TAG, "one-admin"}
_RESIDENT_USER_TAG = "resident-user"

# Firebase ID tokens live ~1h; a claim change is only in the browser's token
# after a refresh (natural rotation, "Re-check my access", or refresh-claims).
_TOKEN_TTL = _dt.timedelta(hours=1)


# ---------------------------------------------------------------------------
# gcloud plumbing
# ---------------------------------------------------------------------------


def _require_gcloud() -> None:
    if shutil.which("gcloud") is None:
        raise click.UsageError(
            "gcloud not found on PATH. Install the Cloud SDK: https://cloud.google.com/sdk/docs/install"
        )


def _access_token() -> str:
    """An OAuth access token for the Identity Toolkit + Firestore REST APIs.

    Application-default credentials first (they usually outlive the hourly
    user-auth token), then the active gcloud account.
    """
    _require_gcloud()
    for cmd in (
        ["gcloud", "auth", "application-default", "print-access-token"],
        ["gcloud", "auth", "print-access-token"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise click.ClickException(
        "Could not mint a gcloud access token. Run `gcloud auth application-default login` "
        "(or `gcloud auth login`) and retry."
    )


# The shipped example config's project ids. Acting on one yields a 403 from
# Identity Toolkit that reads as a permissions problem; it is configuration.
_PLACEHOLDER_PREFIX = "your-project-id"


def _projects(env: str) -> dict[str, str]:
    """env -> GCP project for the envs this command may read (never `local`)."""
    projects: dict[str, str] = _LOG_CFG.get("projects", {})
    if env == "all":
        if not projects:
            raise click.UsageError("No logging.projects configured in cli/config.yaml.")
        chosen = dict(projects)
    else:
        proj = projects.get(env)
        if not proj:
            raise click.UsageError(
                f"No GCP project configured for env '{env}'. Add logging.projects.{env} to cli/config.yaml."
            )
        chosen = {env: proj}
    for e, p in chosen.items():
        if str(p).startswith(_PLACEHOLDER_PREFIX):
            raise click.ClickException(
                f"env '{e}' resolves to the template placeholder project {p!r} — this is configuration, "
                "not credentials. Run `make materialize-config` (then `make cli-install`), or set "
                f"logging.projects.{e} in cli/aiplatform/config.yaml."
            )
    return chosen


def _env_or_fail(ctx: click.Context) -> str:
    env = ctx.obj["env"]
    if env == "local":
        raise click.UsageError(
            "`user` reads a DEPLOYED env's Firebase project. Pass --env dev|test|prod "
            "(before the subcommand: `aiplatform --env prod user show …`)."
        )
    return env


# ---------------------------------------------------------------------------
# Identity Toolkit
# ---------------------------------------------------------------------------


def _idtk(token: str, proj: str, verb: str, *, method: str = "POST", **kw: Any) -> dict[str, Any]:
    url = _IDTK.format(proj=proj, verb=verb)
    headers = {"Authorization": f"Bearer {token}", "x-goog-user-project": proj}
    try:
        resp = httpx.request(method, url, headers=headers, timeout=30, **kw)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"identitytoolkit {verb} failed for {proj}: {exc}") from exc
    if resp.status_code == 403:
        raise click.ClickException(
            f"403 from identitytoolkit on {proj}: your gcloud account needs firebaseauth.users.get "
            "(roles/firebaseauth.viewer) on that project."
        )
    if resp.status_code >= 400:
        raise click.ClickException(f"identitytoolkit {verb} on {proj}: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()


def lookup_by_email(token: str, proj: str, email: str) -> dict[str, Any] | None:
    data = _idtk(token, proj, "lookup", json={"email": [email]})
    users = data.get("users") or []
    return users[0] if users else None


def iter_accounts(token: str, proj: str):
    """Page through every account. Callers MUST filter before printing."""
    page: str | None = None
    while True:
        params = {"maxResults": 1000}
        if page:
            params["nextPageToken"] = page
        data = _idtk(token, proj, "batchGet", method="GET", params=params)
        yield from data.get("users") or []
        page = data.get("nextPageToken")
        if not page:
            return


def _ms(v: Any) -> _dt.datetime | None:
    """Identity Toolkit sends epoch-millis as strings for createdAt/lastLoginAt."""
    if not v:
        return None
    return _dt.datetime.fromtimestamp(int(v) / 1000, _dt.UTC)


def _sec(v: Any) -> _dt.datetime | None:
    if not v:
        return None
    return _dt.datetime.fromtimestamp(int(v), _dt.UTC)


def _rfc3339(v: Any) -> _dt.datetime | None:
    if not v:
        return None
    return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def _fmt(d: _dt.datetime | None) -> str:
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "-"


def _ago(d: _dt.datetime | None, now: _dt.datetime) -> str:
    if not d:
        return ""
    delta = now - d
    secs = int(delta.total_seconds())
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60}m ago"
    if secs < 172800:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def normalise_account(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten an Identity Toolkit account into the fields an operator reads."""
    claims: dict[str, Any] = {}
    if raw.get("customAttributes"):
        try:
            claims = _json.loads(raw["customAttributes"])
        except ValueError:
            claims = {}
    tags = [str(t) for t in (claims.get("groupTags") or [])]
    return {
        "uid": raw.get("localId", ""),
        "email": raw.get("email", ""),
        "display_name": raw.get("displayName", ""),
        "domain": (raw.get("email") or "").split("@")[-1].lower(),
        "disabled": bool(raw.get("disabled")),
        "email_verified": bool(raw.get("emailVerified")),
        "providers": [p.get("providerId", "") for p in raw.get("providerUserInfo") or []],
        "created": _ms(raw.get("createdAt")),
        "last_sign_in": _ms(raw.get("lastLoginAt")),
        "last_refresh": _rfc3339(raw.get("lastRefreshAt")),
        "tokens_valid_since": _sec(raw.get("validSince")),
        "claim_tags": tags,
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# Firestore (derived tags + admin audit trail)
# ---------------------------------------------------------------------------


def _fs_value(v: dict[str, Any]) -> Any:
    """Decode one Firestore REST typed value."""
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "nullValue" in v:
        return None
    if "arrayValue" in v:
        return [_fs_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: _fs_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return v


def _fs_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: _fs_value(v) for k, v in (doc.get("fields") or {}).items()}


def _fs_headers(token: str, proj: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "x-goog-user-project": proj}


def client_config(token: str, proj: str, domain: str) -> dict[str, Any] | None:
    """`clients/{domain}` — where derived group tags + tenant config live."""
    if not domain:
        return None
    url = _FS_DOC.format(proj=proj, path=f"clients/{domain}")
    try:
        resp = httpx.get(url, headers=_fs_headers(token, proj), timeout=30)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"firestore read clients/{domain} failed: {exc}") from exc
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise click.ClickException(f"firestore clients/{domain} on {proj}: HTTP {resp.status_code} {resp.text[:200]}")
    return _fs_fields(resp.json())


def audit_for_target(token: str, proj: str, target: str, limit: int = 5) -> list[dict[str, Any]]:
    """Most recent `admin_audit` records whose target is this email.

    Filters on `target` only and sorts client-side: an equality filter plus an
    `orderBy ts` would demand a composite index the deployment may not carry,
    and a 400 here must never hide the rest of the record.
    """
    body = {
        "structuredQuery": {
            "from": [{"collectionId": "admin_audit"}],
            "where": {
                "fieldFilter": {"field": {"fieldPath": "target"}, "op": "EQUAL", "value": {"stringValue": target}}
            },
            "limit": 200,
        }
    }
    try:
        resp = httpx.post(_FS_QUERY.format(proj=proj), headers=_fs_headers(token, proj), json=body, timeout=30)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"firestore admin_audit query failed: {exc}") from exc
    if resp.status_code >= 400:
        # A missing composite index is the likely cause — degrade to "unknown"
        # rather than hide the whole record behind one optional section.
        click.echo(f"  (audit trail unavailable: HTTP {resp.status_code} {resp.text[:120]})", err=True)
        return []
    out = []
    for row in resp.json():
        doc = row.get("document")
        if doc:
            out.append(_fs_fields(doc))
    out.sort(key=lambda a: str(a.get("ts", "")), reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# Roles + propagation (the lines that answer the ticket)
# ---------------------------------------------------------------------------


def effective_roles(tags: list[str]) -> list[str]:
    """Friendly role lines from a tag set (mirrors backend/auth/admin_roles.py)."""
    s = set(tags)
    lines: list[str] = []
    if _PLATFORM_ADMIN_TAG in s:
        lines.append("PLATFORM ADMIN (aitana-admin) — admin panel: yes, every tenant")
    domains = sorted(t[len(_TENANT_ADMIN_PREFIX) :] for t in s if t.startswith(_TENANT_ADMIN_PREFIX))
    if domains:
        lines.append(f"tenant admin of {', '.join(domains)} — admin panel: yes, own tenant")
    if s & _SKILL_ADMIN_TAGS and _PLATFORM_ADMIN_TAG not in s:
        lines.append("skill admin (one-admin) — may edit skills they don't own; NOT the admin panel")
    if _RESIDENT_USER_TAG in s:
        lines.append("resident-user — eligible for a resident agent")
    if not lines:
        lines.append("no admin role — admin panel: NO (needs aitana-admin or tenant-admin:<domain>)")
    return lines


def _now() -> _dt.datetime:
    """Wall clock, as one seam so tests can pin it (the propagation verdict
    depends on whether a grant is still inside the token TTL)."""
    return _dt.datetime.now(_dt.UTC)


def propagation_line(acct: dict[str, Any], audit: list[dict[str, Any]], now: _dt.datetime) -> str | None:
    """Has the most recent claim change reached the user's browser token yet?

    Firebase only re-mints an ID token on refresh, so a grant made AFTER the
    user's last refresh is invisible to them for up to an hour. Compare the
    latest audit mutation against last_refresh; that one line is the whole
    "I granted it but she still can't see it" ticket.
    """
    last_refresh = acct.get("last_refresh") or acct.get("last_sign_in")
    changes = [a for a in audit if a.get("action") in {"grant_group_tag", "revoke_group_tag", "refresh_claims"}]
    if not changes:
        return None
    latest = changes[0]
    ts = _rfc3339(latest.get("ts"))
    if not ts:
        return None
    who = latest.get("actorEmail") or latest.get("actorUid") or "?"
    what = f"{latest.get('action')} at {_fmt(ts)} by {who}"
    if latest.get("action") == "refresh_claims":
        return f"claims: {what} — user was forced to re-sign-in; propagated on next login"
    if last_refresh and last_refresh >= ts:
        return f"claims: PROPAGATED — token refreshed {_fmt(last_refresh)}, after {what}"
    expires = ts + _TOKEN_TTL
    hint = f"ask them to click 'Re-check my access' on /admin, or run `aiplatform user refresh-claims {acct['email']}`"
    if now < expires:
        return f"claims: NOT YET IN THEIR TOKEN — {what}; token last refreshed {_fmt(last_refresh)}. {hint}"
    return f"claims: {what}; token last refreshed {_fmt(last_refresh)} (>1h ago, will land on next request). {hint}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group("user")
def user() -> None:
    """Who is this person, which env are they on, why can't they see X.

    Reads use your gcloud ADC directly (no backend token). Start here for any
    ticket that names a person rather than a uid.
    """


@user.command("find")
@click.argument("needle")
@click.option(
    "--env",
    "envs",
    type=click.Choice(["all", "dev", "test", "prod"]),
    default="all",
    show_default=True,
    help="Which env's Firebase project to search. Default: every env, so you learn WHERE they signed in.",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def find(needle: str, envs: str, as_json: bool) -> None:
    """Find accounts whose email or display name contains NEEDLE (case-insensitive), across envs.

    \b
      aiplatform user find arias
      aiplatform user find @example.com --env prod
    """
    token = _access_token()
    now = _now()
    pat = re.compile(re.escape(needle), re.IGNORECASE)
    hits: list[dict[str, Any]] = []
    for env, proj in _projects(envs).items():
        for raw in iter_accounts(token, proj):
            if pat.search(raw.get("email") or "") or pat.search(raw.get("displayName") or ""):
                acct = normalise_account(raw)
                acct["env"] = env
                acct["project"] = proj
                hits.append(acct)
    if as_json:
        click.echo(_json.dumps(hits, indent=2, default=str))
        return
    if not hits:
        click.echo(f"No account matching {needle!r} in {', '.join(_projects(envs))}.")
        click.echo("  Tip: try a shorter needle (surname only), or part of the email domain.")
        return
    hits.sort(key=lambda a: (a["email"], a["env"]))
    click.echo(f"{'env':5} {'email':40} {'name':28} {'last sign-in':22} {'':8} tags")
    for a in hits:
        tags = ",".join(a["claim_tags"]) or "-"
        dis = " (DISABLED)" if a["disabled"] else ""
        click.echo(
            f"{a['env']:5} {a['email'][:40]:40} {a['display_name'][:28]:28} "
            f"{_fmt(a['last_sign_in']):22} {_ago(a['last_sign_in'], now):8} {tags}{dis}"
        )
    click.echo("\nNext: aiplatform --env <env> user show <email>")


@user.command("show")
@click.argument("email")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.pass_context
def show(ctx: click.Context, email: str, as_json: bool) -> None:
    """One account on one env: record, claim + derived tags, effective roles, claim propagation.

    \b
      aiplatform --env prod user show msc@example.com
    """
    env = _env_or_fail(ctx)
    proj = _projects(env)[env]
    token = _access_token()
    now = _now()
    raw = lookup_by_email(token, proj, email)
    if raw is None:
        raise click.ClickException(
            f"No account {email!r} on {env} ({proj}). They have never signed in there — "
            f"try `aiplatform user find {email.split('@')[0]}` to see which env they used."
        )
    acct = normalise_account(raw)
    cfg = client_config(token, proj, acct["domain"]) or {}
    derived = [str(t) for t in (cfg.get("derived_group_tags") or [])]
    effective = sorted(set(acct["claim_tags"]) | set(derived))
    audit = audit_for_target(token, proj, email)
    prop = propagation_line(acct, audit, now)

    if as_json:
        out = dict(acct)
        out.update(
            {
                "env": env,
                "project": proj,
                "derived_tags": derived,
                "effective_tags": effective,
                "roles": effective_roles(effective),
                "propagation": prop,
                "client": cfg or None,
                "audit": audit,
            }
        )
        click.echo(_json.dumps(out, indent=2, default=str))
        return

    click.echo(f"{acct['email']}  ({acct['display_name'] or 'no display name'})  env={env}  [ref: {acct['uid']}]")
    flags = []
    if acct["disabled"]:
        flags.append("DISABLED")
    if not acct["email_verified"]:
        flags.append("unverified")
    click.echo(f"  providers:      {', '.join(acct['providers']) or '-'}{'  ' + ' '.join(flags) if flags else ''}")
    click.echo(f"  created:        {_fmt(acct['created'])}")
    click.echo(f"  last sign-in:   {_fmt(acct['last_sign_in'])}  {_ago(acct['last_sign_in'], now)}")
    click.echo(f"  token refresh:  {_fmt(acct['last_refresh'])}  {_ago(acct['last_refresh'], now)}")
    if acct["tokens_valid_since"]:
        click.echo(f"  valid since:    {_fmt(acct['tokens_valid_since'])}  (refresh tokens revoked before this)")
    click.echo(f"  claim tags:     {', '.join(acct['claim_tags']) or '-'}")
    if cfg:
        click.echo(
            f"  derived tags:   {', '.join(derived) or '-'}  (from clients/{acct['domain']}"
            f"{' — ' + str(cfg.get('display_name')) if cfg.get('display_name') else ''})"
        )
        if cfg.get("default_skill"):
            click.echo(
                f"  tenant default: {cfg['default_skill']}  enabled: {', '.join(cfg.get('enabled_skills') or [])}"
            )
    else:
        click.echo(f"  derived tags:   -  (no clients/{acct['domain']} doc: not an onboarded tenant)")
    click.echo("  roles:")
    for line in effective_roles(effective):
        click.echo(f"    - {line}")
    if prop:
        click.echo(f"  {prop}")
    if audit:
        click.echo("  recent admin actions on this account:")
        for a in audit:
            click.echo(f"    {a.get('ts', '')[:16]}  {a.get('action')}  by {a.get('actorEmail') or a.get('actorUid')}")
    click.echo(f"\nNext: aiplatform --env {env} user activity {email} --since 2h")


def _collapse_auth_lines(lines: list[tuple[str, str]]) -> list[str]:
    """Fold `auth: authenticated uid=…` lines into one summary per minute.

    Every proxied request logs one; the interesting information is WHEN they
    were active, not 40 identical lines. Other lines (route hits carrying the
    uid) stay in place, so a minute reads as "active (N)" followed by what
    they actually called.
    """
    per_minute: dict[str, int] = {}
    for ts, text in lines:
        if "auth: authenticated uid=" in text:
            per_minute[ts[:16]] = per_minute.get(ts[:16], 0) + 1
    out: list[str] = []
    seen: set[str] = set()
    for ts, text in lines:
        minute = ts[:16]
        if "auth: authenticated uid=" in text:
            if minute not in seen:
                seen.add(minute)
                n = per_minute[minute]
                out.append(f"{minute}     active — {n} authenticated request{'s' if n != 1 else ''}")
            continue
        out.append(f"{ts[:19]}  {text}")
    return out


@user.command("activity")
@click.argument("email")
@click.option("--since", default="2h", show_default=True, help="How far back (gcloud --freshness): 30m, 2h, 1d.")
@click.option("--limit", "-n", default=300, show_default=True, help="Max raw log entries to scan.")
@click.option("--raw", is_flag=True, help="Print every matching line instead of collapsing auth runs.")
@click.pass_context
def activity(ctx: click.Context, email: str, since: str, limit: int, raw: bool) -> None:
    """What this person did on the deployed env: sign-ins, admin-panel probes, request bursts.

    Resolves email -> uid, then reads the backend container's lines carrying that
    uid. Browser-side events are a separate stream: `logs client --uid <uid>`.
    """
    env = _env_or_fail(ctx)
    proj = _projects(env)[env]
    token = _access_token()
    acct_raw = lookup_by_email(token, proj, email)
    if acct_raw is None:
        raise click.ClickException(f"No account {email!r} on {env} ({proj}).")
    uid = acct_raw["localId"]

    # Same targeting as `aiplatform logs` (service + sidecar container).
    from aiplatform.commands.logs import _gcloud_read, _resolve_target

    _proj, svc, container = _resolve_target(env, None, None, "backend")
    log_filter = " AND ".join(
        [
            'resource.type="cloud_run_revision"',
            f'resource.labels.service_name="{svc}"',
            f'labels.container_name="{container}"',
            f'(textPayload:"{uid}" OR jsonPayload.uid="{uid}" OR jsonPayload.message:"{uid}")',
        ]
    )
    out = _gcloud_read(log_filter, proj, since, limit, "value(timestamp,textPayload,jsonPayload.message)")
    entries: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        ts = parts[0]
        text = next((p for p in parts[1:] if p.strip()), "")
        entries.append((ts, text.strip()))
    entries.sort()  # gcloud returns newest first; a timeline reads oldest first
    click.echo(f"{email} [ref: {uid}] on {env}, last {since}: {len(entries)} backend line(s)")
    if not entries:
        click.echo(
            "  nothing — they did not reach this env's backend in that window. Try --since 1d, or another --env."
        )
        return
    rendered = [f"{t[:19]}  {x}" for t, x in entries] if raw else _collapse_auth_lines(entries)
    for line in rendered:
        click.echo(f"  {line}")
    click.echo(
        f"\nBrowser-side events: aiplatform --env {env} logs client --uid {uid} --since {since}\n"
        f"Admin-panel probes are GET /api/admin/whoami — see `aiplatform --env {env} logs --container ui -g /api/admin`."
    )


@user.command("refresh-claims")
@click.argument("email")
@click.pass_context
def refresh_claims(ctx: click.Context, email: str) -> None:
    """Force a just-granted/revoked tag to take effect NOW (revokes their refresh tokens).

    The one mutation in this group. Goes through the audited backend route, so
    it needs an aitana-admin AIPLATFORM_ID_TOKEN like `groups grant` does.
    """
    result = AIPlatformClient(env=ctx.obj["env"]).post(f"/api/admin/users/{email}/refresh-claims")
    click.echo(_json.dumps(result, indent=2))
    click.echo(f"\n{email} must sign in again; their next token carries the current claims.")
