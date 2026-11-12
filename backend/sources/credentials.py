"""The GitHub credentials a deployment offers to sources, and who may use them
(github-sources.md §6, §16).

A credential is deployment configuration, never data a user supplies. Each one
names **what it may read** (``repos``: ``owner/name`` globs; ``!owner/name`` leaves
one out) and **who may use it**
(``users``: the platform's access rule — ``tagged``, ``specific``, ``domain`` or
``public``; absent means nobody). Both are checked when a source is added and
again on every sync, against the source owner's *current* access, so withdrawing
someone's tag or narrowing ``repos`` takes effect on the next run.

Without that, a credential's reach would be every user's reach: anyone able to
add a source could point it at any repository the credential can read.

Behind a credential sits either

* a **GitHub App** (preferred): ``app_id``, its private key (``private_key_env``
  or ``private_key_file``) and optionally ``installation_id``. Each sync gets a
  one-hour installation token limited to the one repository and to
  *Contents: read* — nothing long-lived, and nothing tied to a person; or
* a **fine-grained token** in an environment variable (``token_env``), with
  *Contents: read* on the repositories it is meant for.

The configuration is ``GITHUB_SOURCE_CREDENTIALS``: JSON, or the path of a JSON or
YAML file. On Cloud Run, mount it (and the private key) from Secret Manager::

    credentials:
      docs:
        app_id: "123456"
        private_key_env: GITHUB_APP_PRIVATE_KEY
        repos: ["example-org/*", "!example-org/clients"]
        users: {type: tagged, tags: [staff]}
      client-files:
        app_id: "123456"
        private_key_env: GITHUB_APP_PRIVATE_KEY
        repos: ["example-org/clients"]
        users: {type: specific, emails: [lead@example.com]}

A knowledge-base source (operator config) names a credential too; ``repos`` still
applies there, ``users`` does not (the operator is not a platform user).

**Write credentials** (``access: write``; project-source-write.md §3) let a
project's agent commit to a writable source. Only a GitHub App can stand behind
one: each write mints a token for the one repository with *Contents: write*. The
App's own installation is the outer limit — a repository it isn't installed on
can't be written whatever ``repos`` says — so ``repos`` can stay broad and the
installation decides::

      docs-write:
        app_id: "654321"                 # a separate writer App
        private_key_env: GITHUB_WRITER_APP_PRIVATE_KEY
        access: write
        repos: ["example-org/*", "!example-org/clients"]
        users: {type: tagged, tags: [staff]}
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auth.access_context import AccessContext, can_access
from db.models.access import AccessControl

log = logging.getLogger(__name__)

CONFIG_ENV = "GITHUB_SOURCE_CREDENTIALS"
_REF = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class CredentialConfigError(ValueError):
    """The credentials configuration can't be used. Logged; no credential is offered."""


@dataclass(frozen=True)
class Credential:
    ref: str
    repos: tuple[str, ...]
    users: AccessControl = field(default_factory=AccessControl)
    token_env: str = ""
    app_id: str = ""
    installation_id: str = ""
    private_key_env: str = ""
    private_key_file: str = ""
    access: str = "read"

    @property
    def is_app(self) -> bool:
        return bool(self.app_id)

    @property
    def can_write(self) -> bool:
        return self.access == "write"

    def covers(self, repo: str) -> bool:
        """In some pattern and in no ``!pattern`` — so ``["org/*", "!org/clients"]``
        covers every repository of ``org``, including new ones, except one."""
        repo = repo.lower()
        allowed = [p for p in self.repos if not p.startswith("!")]
        denied = [p[1:] for p in self.repos if p.startswith("!")]
        return any(fnmatch.fnmatchcase(repo, p) for p in allowed) and not any(
            fnmatch.fnmatchcase(repo, p) for p in denied
        )

    def usable_by(self, ctx: AccessContext) -> bool:
        return can_access(self.users, ctx, "")

    def describe(self) -> dict[str, Any]:
        return {"ref": self.ref, "repos": list(self.repos)}

    def private_key(self) -> str:
        if self.private_key_env:
            key = os.environ.get(self.private_key_env, "")
        elif self.private_key_file:
            key = Path(self.private_key_file).expanduser().read_text()
        else:
            key = ""
        # A key pasted into a one-line env var keeps its newlines as "\n".
        return key.replace("\\n", "\n").strip()


def _parse(raw: str) -> dict[str, Credential]:
    text = raw.strip()
    if not text.startswith("{"):
        path = Path(text).expanduser()
        if not path.is_file():
            raise CredentialConfigError(f"{CONFIG_ENV} is neither JSON nor a readable file: {text!r}")
        text = path.read_text()
    try:
        data = json.loads(text) if text.lstrip().startswith("{") else _yaml(text)
    except ValueError as exc:
        raise CredentialConfigError(f"{CONFIG_ENV} could not be parsed: {exc}") from exc
    rows = (data or {}).get("credentials") or {}
    if not isinstance(rows, dict):
        raise CredentialConfigError("'credentials' must map names to credentials")
    out: dict[str, Credential] = {}
    for ref, row in rows.items():
        out[str(ref)] = _credential(str(ref), row if isinstance(row, dict) else {})
    return out


def _yaml(text: str) -> Any:
    import yaml

    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc


def _credential(ref: str, row: dict[str, Any]) -> Credential:
    where = f"credential {ref!r}"
    if not _REF.match(ref):
        raise CredentialConfigError(f"{where}: names are lowercase letters, digits and hyphens")
    repos = tuple(str(r).strip().lower() for r in row.get("repos") or [] if str(r).strip())
    if not any(not r.startswith("!") for r in repos) or any(r.lstrip("!").count("/") != 1 for r in repos):
        raise CredentialConfigError(
            f"{where}: 'repos' must list owner/name patterns, e.g. example-org/* (and !example-org/private to leave one out)"
        )
    try:
        users = AccessControl(**(row.get("users") or {"type": "private"}))
    except ValueError as exc:
        raise CredentialConfigError(f"{where}: 'users' is not an access rule: {exc}") from exc
    access = str(row.get("access") or "read").strip().lower()
    if access not in ("read", "write"):
        raise CredentialConfigError(f"{where}: 'access' is read or write")
    cred = Credential(
        ref=ref,
        access=access,
        repos=repos,
        users=users,
        token_env=str(row.get("token_env") or ""),
        app_id=str(row.get("app_id") or ""),
        installation_id=str(row.get("installation_id") or ""),
        private_key_env=str(row.get("private_key_env") or ""),
        private_key_file=str(row.get("private_key_file") or ""),
    )
    if cred.is_app == bool(cred.token_env):
        raise CredentialConfigError(f"{where}: give either a GitHub App (app_id + private key) or token_env, not both")
    if cred.is_app and not (cred.private_key_env or cred.private_key_file):
        raise CredentialConfigError(f"{where}: a GitHub App needs private_key_env or private_key_file")
    if cred.can_write and not cred.is_app:
        raise CredentialConfigError(
            f"{where}: only a GitHub App can write (a token's reach can't be limited per write)"
        )
    return cred


_cache: tuple[str, dict[str, Credential]] | None = None


def load() -> dict[str, Credential]:
    """Every configured credential; {} when none or when the configuration is
    broken (logged once per distinct configuration — never fail open)."""
    global _cache
    raw = os.environ.get(CONFIG_ENV, "").strip()
    if not raw:
        return {}
    if _cache is not None and _cache[0] == raw:
        return _cache[1]
    try:
        creds = _parse(raw)
    except (CredentialConfigError, OSError) as exc:
        log.error("GitHub source credentials unusable, none offered: %s", exc)
        creds = {}
    _cache = (raw, creds)
    return creds


def get(ref: str) -> Credential | None:
    return load().get((ref or "").strip().lower())


def offered(ctx: AccessContext) -> list[Credential]:
    """The credentials this person may use to READ, sorted by name. Write
    credentials are left out: they are chosen for writing, never for a sync."""
    return [c for _ref, c in sorted(load().items()) if c.usable_by(ctx) and not c.can_write]


def writers(ctx: AccessContext) -> list[Credential]:
    """The write credentials this person may use, sorted by name."""
    return [c for _ref, c in sorted(load().items()) if c.can_write and c.usable_by(ctx)]


def writer_for(ctx: AccessContext, repo: str) -> Credential | None:
    """The write credential this person may use on ``repo``, if any (first by name)."""
    return next(
        (c for _ref, c in sorted(load().items()) if c.can_write and c.usable_by(ctx) and c.covers(repo)),
        None,
    )


async def token(cred: Credential, repo: str) -> str:
    """A token that can read ``repo`` through ``cred``. Raises ``GitHubError``."""
    from sources.github import GitHubError

    if not cred.covers(repo):
        raise GitHubError("SOURCE_REPO_NOT_COVERED", f"The {cred.ref!r} access doesn't include {repo}.", 403)
    if cred.is_app:
        from sources.github_app import installation_token

        return await installation_token(cred, repo)
    value = os.environ.get(cred.token_env, "").strip()
    if not value:
        raise GitHubError("SOURCE_CREDENTIAL_MISSING", f"The token for {cred.ref!r} is not configured.", 409)
    return value


async def write_token(cred: Credential, repo: str) -> str:
    """A token that can write the contents of ``repo`` and nothing else. Raises ``GitHubError``."""
    from sources.github import GitHubError
    from sources.github_app import installation_token

    if not cred.can_write:
        raise GitHubError("SOURCE_WRITE_NOT_ALLOWED", f"The {cred.ref!r} access is read-only.", 403)
    if not cred.covers(repo):
        raise GitHubError("SOURCE_WRITE_NOT_ALLOWED", f"The {cred.ref!r} access doesn't include {repo}.", 403)
    return await installation_token(cred, repo, permissions={"contents": "write"})
