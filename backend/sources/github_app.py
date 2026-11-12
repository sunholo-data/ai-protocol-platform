"""GitHub App installation tokens for sources (github-sources.md §16).

The platform signs a ten-minute JWT with the App's private key, finds the App's
installation on the repository's owner (or uses the configured one), and asks
for an installation token limited to that ONE repository and to *Contents: read*.
GitHub gives it for an hour; it is cached until five minutes before that. Nothing
long-lived leaves the deployment's secret store, and no person's account is
involved.

A write credential (project-source-write.md §3) gets the same kind of token with
*Contents: write* (``installation_token(..., permissions=...)``). ``installed_on``
and ``install_url`` let the project page say when the writer App still has to be
installed on a repository, and link to where that is done.

An MCP server with ``auth: github_app`` (tools/mcp/github_app_auth.py) asks for
one token over several repositories instead: ``scoped_token`` names exactly the
repositories the caller may read, with read-only permissions.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
import jwt

from sources.credentials import Credential
from sources.github import API, GitHubError

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_REFRESH_MARGIN_S = 300
_REPOSITORIES_TTL_S = 600
# GitHub's own limit on the repositories one installation token may name.
_MAX_TOKEN_REPOSITORIES = 500
_tokens: dict[tuple[str, ...], tuple[str, float]] = {}
_installations: dict[tuple[str, str], str] = {}
_repositories: dict[tuple[str, ...], tuple[float, list[str]]] = {}
_INSTALLED_TTL_S = 300
_installed: dict[tuple[str, str], tuple[float, bool]] = {}
_slugs: dict[str, str] = {}


def app_jwt(cred: Credential, now: float | None = None) -> str:
    key = cred.private_key()
    if not key:
        raise GitHubError("SOURCE_CREDENTIAL_MISSING", f"The private key for {cred.ref!r} is not configured.", 409)
    now = time.time() if now is None else now
    # iat a minute back absorbs clock skew; GitHub caps exp at ten minutes.
    claims = {"iat": int(now) - 60, "exp": int(now) + 540, "iss": cred.app_id}
    try:
        return jwt.encode(claims, key, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GitHubError(
            "SOURCE_CREDENTIAL_INVALID", f"The private key for {cred.ref!r} can't sign: {exc}", 409
        ) from exc


def _headers(bearer: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-protocol-platform-sources",
    }


async def _call(method: str, url: str, bearer: str, *, json: Any = None, http: httpx.AsyncClient | None = None) -> Any:
    try:
        if http is not None:
            resp = await http.request(method, url, headers=_headers(bearer), json=json, timeout=_TIMEOUT)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, headers=_headers(bearer), json=json, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GitHubError("GITHUB_UNREACHABLE", f"GitHub could not be reached: {exc}") from exc
    if resp.is_success:
        return resp.json()
    if resp.status_code == 401:
        raise GitHubError("SOURCE_CREDENTIAL_REJECTED", "GitHub rejected the App's credentials (app id or key).", 502)
    if resp.status_code in (404, 422):
        # Not installed on that owner, or the repository isn't in the installation.
        raise GitHubError(
            "SOURCE_NOT_VISIBLE",
            "The GitHub App can't read that repository: install it on the repository's owner and include the repository.",
            404,
        )
    raise GitHubError("GITHUB_ERROR", f"GitHub answered {resp.status_code} to the App.", 502)


async def _mint(
    cred: Credential, installation: str, body: dict[str, Any], *, http: httpx.AsyncClient | None
) -> tuple[str, float]:
    granted = await _call(
        "POST", f"{API}/app/installations/{installation}/access_tokens", app_jwt(cred), json=body, http=http
    )
    expires = datetime.fromisoformat(str(granted["expires_at"]).replace("Z", "+00:00")).timestamp()
    return str(granted["token"]), expires


def _cached(key: tuple[str, ...]) -> str | None:
    cached = _tokens.get(key)
    if cached and cached[1] - _REFRESH_MARGIN_S > time.time():
        return cached[0]
    return None


async def installation_token(
    cred: Credential,
    repo: str,
    *,
    permissions: dict[str, str] | None = None,
    http: httpx.AsyncClient | None = None,
) -> str:
    """A token for the one repository ``repo``, with ``permissions`` (default
    *Contents: read*)."""
    permissions = permissions or {"contents": "read"}
    key = (cred.ref, repo.lower(), ",".join(f"{k}={v}" for k, v in sorted(permissions.items())))
    # No lock: two concurrent misses mint two tokens, which is harmless, and a
    # lock would tie this to one event loop (the KB sync runs its own).
    if cached := _cached(key):
        return cached
    owner, name = repo.split("/", 1)
    installation = cred.installation_id or _installations.get((cred.ref, owner.lower()))
    if not installation:
        found = await _call("GET", f"{API}/repos/{repo}/installation", app_jwt(cred), http=http)
        installation = str(found["id"])
        _installations[(cred.ref, owner.lower())] = installation
    body = {"repositories": [name], "permissions": dict(permissions)}
    value, expires = await _mint(cred, installation, body, http=http)
    _tokens[key] = (value, expires)
    return value


async def installed_on(cred: Credential, repo: str, *, http: httpx.AsyncClient | None = None) -> bool:
    """Whether the App is installed with access to ``repo``. Cached for five
    minutes, so adding the repository to the installation shows up soon after."""
    key = (cred.ref, repo.lower())
    hit = _installed.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        found = await _call("GET", f"{API}/repos/{repo}/installation", app_jwt(cred), http=http)
        _installations[(cred.ref, repo.split("/", 1)[0].lower())] = str(found["id"])
        ok = True
    except GitHubError as exc:
        if exc.code != "SOURCE_NOT_VISIBLE":
            raise
        ok = False
    _installed[key] = (time.time() + _INSTALLED_TTL_S, ok)
    return ok


async def install_url(cred: Credential, *, http: httpx.AsyncClient | None = None) -> str:
    """Where an owner installs the App, or adds a repository to its installation."""
    slug = _slugs.get(cred.ref)
    if slug is None:
        data = await _call("GET", f"{API}/app", app_jwt(cred), http=http)
        slug = _slugs[cred.ref] = str(data.get("slug") or "")
    return f"https://github.com/apps/{slug}/installations/new" if slug else ""


# --- Tokens for several repositories (MCP servers, tools/mcp/github_app_auth.py) ---


async def owner_installation(cred: Credential, owner: str, *, http: httpx.AsyncClient | None = None) -> str:
    """The App's installation on ``owner`` (an organisation or a user account)."""
    if cred.installation_id:
        return cred.installation_id
    key = (cred.ref, owner.lower())
    if found := _installations.get(key):
        return found
    try:
        data = await _call("GET", f"{API}/orgs/{owner}/installation", app_jwt(cred), http=http)
    except GitHubError as exc:
        if exc.code != "SOURCE_NOT_VISIBLE":
            raise
        data = await _call("GET", f"{API}/users/{owner}/installation", app_jwt(cred), http=http)
    _installations[key] = str(data["id"])
    return _installations[key]


async def installation_repositories(
    cred: Credential, installation: str, *, http: httpx.AsyncClient | None = None
) -> list[str]:
    """Every repository the installation can see, as lowercase ``owner/name``.

    Listed with a token that can read metadata and nothing else, and cached for
    ten minutes so a repository added to the installation shows up soon after."""
    key = ("repos", cred.ref, installation)
    listed = _repositories.get(key)
    if listed and listed[0] > time.time():
        return listed[1]
    bearer, _ = await _mint(cred, installation, {"permissions": {"metadata": "read"}}, http=http)
    names: list[str] = []
    page = 1
    while True:
        data = await _call("GET", f"{API}/installation/repositories?per_page=100&page={page}", bearer, http=http)
        batch = data.get("repositories") or []
        names.extend(str(r["full_name"]).lower() for r in batch)
        if len(batch) < 100 or len(names) >= int(data.get("total_count") or 0):
            break
        page += 1
    _repositories[key] = (time.time() + _REPOSITORIES_TTL_S, names)
    return names


async def scoped_token(
    cred: Credential,
    installation: str,
    names: list[str],
    permissions: dict[str, str],
    *,
    http: httpx.AsyncClient | None = None,
) -> str:
    """An installation token limited to ``names`` (repository names on the
    installation's account) and ``permissions``. Cached until shortly before it
    expires, so the same caller gets the same token for the best part of an hour."""
    if not names:
        raise GitHubError("SOURCE_REPO_NOT_COVERED", "No repositories to ask for.", 403)
    if len(names) > _MAX_TOKEN_REPOSITORIES:
        raise GitHubError(
            "SOURCE_TOO_MANY_REPOSITORIES",
            f"GitHub limits a token to {_MAX_TOKEN_REPOSITORIES} repositories; this asks for {len(names)}. "
            "Narrow the credential's repos.",
            409,
        )
    ordered = sorted(n.lower() for n in names)
    key = (cred.ref, installation, ",".join(ordered), ",".join(f"{k}={v}" for k, v in sorted(permissions.items())))
    if cached := _cached(key):
        return cached
    body = {"repositories": ordered, "permissions": dict(permissions)}
    value, expires = await _mint(cred, installation, body, http=http)
    _tokens[key] = (value, expires)
    return value


def _forget_cached() -> None:
    """Tests only."""
    _tokens.clear()
    _installations.clear()
    _repositories.clear()
    _installed.clear()
    _slugs.clear()
