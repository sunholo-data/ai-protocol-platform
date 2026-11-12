"""GitHub App tokens for MCP servers: ``auth: "github_app"`` on an ``mcp_servers`` row.

GitHub's hosted MCP server (``https://api.githubcopilot.com/mcp/readonly``)
accepts a GitHub App installation token. This module mints one per caller from
the deployment's GitHub source credentials (``GITHUB_SOURCE_CREDENTIALS``,
sources/credentials.py), so the server reads exactly what that person could add
as a source and nothing more:

* only App credentials whose ``users`` rule admits the caller count;
* the token names every repository the installation can see that one of those
  credentials ``covers`` (``!owner/name`` exclusions hold);
* its permissions are read-only — ``contents: read`` unless the row asks for
  more, and a row asking for anything but ``read`` is refused.

A row may narrow further with ``credentials: [ref, ...]``. All credentials that
admit the caller must sit on one installation (one GitHub account), because a
token belongs to one installation; set ``installation_id`` on the row to choose
when they don't.

Nothing is stored: the App's private key stays in the deployment's secret store,
the token lives in memory for under an hour, and no person's GitHub account is
involved. A per-user OAuth flow (each person signs in to GitHub) is a different
feature; this one makes the deployment's App the reader, under the platform's
own access rules.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from auth.access_context import AccessContext
from sources import credentials, github_app
from sources.credentials import Credential
from sources.github import GitHubError

log = logging.getLogger(__name__)

AUTH_MODE = "github_app"
DEFAULT_PERMISSIONS: dict[str, str] = {"contents": "read"}
_PERMISSION_NAME = re.compile(r"^[a-z_]{1,64}$")
_GLOB = re.compile(r"[*?\[]")


class GitHubMcpAccessError(RuntimeError):
    """This caller can't be given a GitHub token for this server. The message
    says why in words a person can act on."""


def permissions(config: dict[str, Any]) -> dict[str, str]:
    """The row's permissions, read-only or refused."""
    wanted = config.get("permissions") or DEFAULT_PERMISSIONS
    if not isinstance(wanted, dict) or not wanted:
        raise GitHubMcpAccessError("'permissions' must map GitHub permission names to \"read\".")
    out: dict[str, str] = {}
    for name, level in wanted.items():
        name, level = str(name).strip().lower(), str(level).strip().lower()
        if not _PERMISSION_NAME.match(name) or level != "read":
            raise GitHubMcpAccessError(f"GitHub MCP servers are read-only: {name}={level!r} is not allowed.")
        out[name] = level
    return out


def usable_credentials(ctx: AccessContext, config: dict[str, Any]) -> list[Credential]:
    """App credentials this caller may use, narrowed by the row's ``credentials``.
    Write credentials are never used here: this server only reads, and the writer
    App's installation is a different one (project-source-write.md §3)."""
    named = {str(r).strip().lower() for r in config.get("credentials") or [] if str(r).strip()}
    return [
        cred
        for ref, cred in sorted(credentials.load().items())
        if cred.is_app and not cred.can_write and (not named or ref in named) and cred.usable_by(ctx)
    ]


def _owner(cred: Credential) -> str:
    owners = {p.split("/", 1)[0] for p in cred.repos if not p.startswith("!")}
    if len(owners) != 1 or _GLOB.search(next(iter(owners))):
        raise GitHubMcpAccessError(
            f"Credential {cred.ref!r} spans several GitHub accounts; give it an installation_id."
        )
    return owners.pop()


async def _installation(cred: Credential, http: httpx.AsyncClient | None) -> str:
    return cred.installation_id or await github_app.owner_installation(cred, _owner(cred), http=http)


async def token_for(ctx: AccessContext, config: dict[str, Any], *, http: httpx.AsyncClient | None = None) -> str:
    """A read-only installation token over every repository this caller may read."""
    wanted = permissions(config)
    creds = usable_credentials(ctx, config)
    if not creds:
        raise GitHubMcpAccessError("You don't have access to any GitHub repositories on this platform.")
    by_installation: dict[str, list[Credential]] = {}
    for cred in creds:
        by_installation.setdefault(await _installation(cred, http), []).append(cred)
    pinned = str(config.get("installation_id") or "").strip()
    if pinned:
        if pinned not in by_installation:
            raise GitHubMcpAccessError("You don't have access to any repositories on this GitHub account.")
        installation = pinned
    elif len(by_installation) > 1:
        raise GitHubMcpAccessError(
            "Your GitHub access spans several GitHub accounts; set installation_id on the MCP server to choose one."
        )
    else:
        installation = next(iter(by_installation))
    group = by_installation[installation]
    visible = await github_app.installation_repositories(group[0], installation, http=http)
    allowed = sorted(r for r in visible if any(c.covers(r) for c in group))
    if not allowed:
        raise GitHubMcpAccessError("None of the repositories you may read are installed for the GitHub App.")
    names = [r.split("/", 1)[1] for r in allowed]
    return await github_app.scoped_token(group[0], installation, names, wanted, http=http)


def header_provider(server_id: str, config: dict[str, Any], ctx: AccessContext):
    """ADK per-call header provider (async) carrying this caller's token.

    The token is cached, so the headers are stable for most of an hour and ADK's
    MCP session pool (keyed on the headers) keeps reusing one session."""

    async def _provider(_readonly_context) -> dict[str, str]:
        try:
            token = await token_for(ctx, config)
        except GitHubError as exc:
            log.error("mcp_registry: GitHub token for %s failed: %s (%s)", server_id, exc.message, exc.code)
            raise GitHubMcpAccessError(exc.message) from exc
        except GitHubMcpAccessError as exc:
            log.warning("mcp_registry: no GitHub token for %s, caller=%s: %s", server_id, ctx.email, exc)
            raise
        return {"Authorization": f"Bearer {token}"}

    return _provider
