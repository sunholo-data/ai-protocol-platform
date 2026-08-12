"""Resolve Firebase/deployment config for the smoke, seed and admin scripts.

Why this exists (template-fork-ergonomics G23 Part A): `whoami_smoke.py` and
`verify_rules.py` used to import `_env.py` directly, and `_env.py` holds
per-environment Firebase Web API keys for THIS deployment. The public template
excludes `_env.py`, which meant the two smoke scripts (and the four ops docs
that reference them) had to be excluded too — so a fork inherited the auth
system with no way to smoke-test it end-to-end.

This module makes `_env.py` OPTIONAL. Resolution order, first hit wins:

1. Explicit CLI arguments (`--url` / `--api-key` / `--project-id`).
2. A named environment from `_env.py`, when that file is present (the
   internal-deployment convenience path; absent in a template fork).
3. Environment variables — including the same `NEXT_PUBLIC_FIREBASE_*` names
   the frontend already needs, so a fork that can run the app can run the
   smoke with no extra setup.
4. `frontend/.env.local`, if present, parsed for those same names.

A fork therefore does nothing special: `make dev` already requires the
NEXT_PUBLIC_FIREBASE_* values, and the smoke picks them up from there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `_env.py` is deployment-private and excluded from the public template. Import
# it when present, degrade to "no named environments" when not — the same
# optional-loader pattern backend/db/local_fixture.py uses for customer skills.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from _env import ENVIRONMENTS  # type: ignore[import-not-found]
    from _env import SMOKE_IDENTITY as _DEPLOYMENT_IDENTITY  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised only in a template fork
    ENVIRONMENTS: dict[str, dict[str, str]] = {}
    # Neutral defaults for a fork. `.test` is RFC 2606 reserved.
    _DEPLOYMENT_IDENTITY: dict[str, object] = {
        "email": "whoami-test@platform.test",
        "group_tags": ["platform-admin-test"],
    }

HAVE_NAMED_ENVS = bool(ENVIRONMENTS)

_REPO_ROOT = _HERE.parent.parent
_FRONTEND_ENV = _REPO_ROOT / "frontend" / ".env.local"

_API_KEY_VARS = ("FIREBASE_API_KEY", "NEXT_PUBLIC_FIREBASE_API_KEY")
_PROJECT_VARS = (
    "FIREBASE_PROJECT_ID",
    "NEXT_PUBLIC_FIREBASE_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GCP_PROJECT",
)
_URL_VARS = ("SMOKE_BASE_URL", "BASE_URL", "FRONTEND_URL")


class SmokeConfigError(RuntimeError):
    """Raised when config can't be resolved from any source."""


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file. Tolerates comments, blanks, quotes, `export`."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _first(names: tuple[str, ...], *sources: dict[str, str]) -> str | None:
    for source in sources:
        for name in names:
            value = source.get(name)
            if value:
                return value
    return None


def resolve(
    *,
    env: str | None = None,
    url: str | None = None,
    api_key: str | None = None,
    project_id: str | None = None,
) -> tuple[str, str, str]:
    """Return `(base_url, api_key, project_id)`.

    Raises SmokeConfigError with actionable guidance when a value is missing —
    fail loud, never fall back to a wrong project silently.
    """
    if env:
        if env not in ENVIRONMENTS:
            known = ", ".join(sorted(ENVIRONMENTS)) if ENVIRONMENTS else "(none — backend/scripts/_env.py is absent)"
            raise SmokeConfigError(f"unknown env {env!r}. Known: {known}")
        cfg = ENVIRONMENTS[env]
        return (url or cfg["url"], api_key or cfg["api_key"], project_id or cfg["project_id"])

    dotenv = _read_dotenv(_FRONTEND_ENV)
    resolved_url = url or _first(_URL_VARS, os.environ, dotenv)
    resolved_key = api_key or _first(_API_KEY_VARS, os.environ, dotenv)
    resolved_project = project_id or _first(_PROJECT_VARS, os.environ, dotenv)

    missing = [
        name
        for name, value in (
            ("--url / SMOKE_BASE_URL", resolved_url),
            ("--api-key / NEXT_PUBLIC_FIREBASE_API_KEY", resolved_key),
            ("--project-id / NEXT_PUBLIC_FIREBASE_PROJECT_ID", resolved_project),
        )
        if not value
    ]
    if missing:
        # Show the dotenv path repo-relative when we can, absolute otherwise —
        # never let a path-formatting detail raise and mask the real error.
        try:
            dotenv_display = _FRONTEND_ENV.relative_to(_REPO_ROOT)
        except ValueError:
            dotenv_display = _FRONTEND_ENV

        options = ["pass --env dev"] if HAVE_NAMED_ENVS else []
        options += [
            "pass the flags explicitly",
            "set the environment variables above",
            f"put them in {dotenv_display} (the same values the frontend build needs)",
        ]
        raise SmokeConfigError(
            "could not resolve smoke config. Missing: "
            + ", ".join(missing)
            + ".\n  Fix by any one of:\n    - "
            + "\n    - ".join(options)
        )
    return (resolved_url, resolved_key, resolved_project)  # type: ignore[return-value]


def project_for_env(env: str | None) -> str:
    """Return the GCP project for `env`, or the ambient project when unnamed.

    In a template fork there are no named environments, so the project comes
    from GOOGLE_CLOUD_PROJECT / GCP_PROJECT. Raises rather than guessing.
    """
    if env and env in ENVIRONMENTS:
        return ENVIRONMENTS[env]["project_id"]
    ambient = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    if ambient:
        return ambient
    if ENVIRONMENTS:
        raise SmokeConfigError(f"unknown env {env!r}. Known: {', '.join(sorted(ENVIRONMENTS))}")
    raise SmokeConfigError(
        "no GCP project. There are no named environments (backend/scripts/_env.py "
        "is deployment-private), so set GOOGLE_CLOUD_PROJECT (or GCP_PROJECT) "
        "to the project this script should write to."
    )


def pin_project_for_env(env: str | None) -> None:
    """Force GCP_PROJECT + GOOGLE_CLOUD_PROJECT to the canonical project for `env`.

    Why: db scripts that read/write Firestore inherit GCP_PROJECT from the
    shell, and many developers have GCP_PROJECT pointing somewhere else for
    other tooling. A silent shadow lets a "seed dev skills" script cheerfully
    write to the WRONG project. Pin here at the top of every db-touching script
    so the wrong project cannot even be selected.

    Behaviour:
      * GCP_PROJECT unset          → set both vars to the expected project.
      * GCP_PROJECT matches        → set GOOGLE_CLOUD_PROJECT defensively, no-op.
      * GCP_PROJECT mismatches     → exit(2) with a clear message; do NOT
        silently rewrite (the shell value is intentional for OTHER tooling in
        the same shell, and rewriting could confuse it after this returns).

    Lives here rather than in `_env.py` so it still works in a template fork,
    where `_env.py` is absent but scripts still need the anti-shadow guard.

    Raises SystemExit(2) on mismatch. Callers don't need to handle errors.
    """
    try:
        expected = project_for_env(env)
    except SmokeConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    current = os.environ.get("GCP_PROJECT")
    if current and current != expected:
        target = f"{env}={expected!r}" if env and ENVIRONMENTS else repr(expected)
        print(
            f"ERROR: GCP_PROJECT is set to {current!r} but this script targets {target}.\n"
            f"  Run with the right project explicitly:\n"
            f"    GCP_PROJECT={expected} GOOGLE_CLOUD_PROJECT={expected} \\\n"
            f"      uv run python {sys.argv[0]}\n"
            f"  Or unset GCP_PROJECT in your shell so the script can set it.",
            file=sys.stderr,
        )
        sys.exit(2)
    os.environ["GCP_PROJECT"] = expected
    os.environ["GOOGLE_CLOUD_PROJECT"] = expected


def test_identity(email: str | None = None, group_tags: list[str] | None = None) -> tuple[str, str, list[str]]:
    """Return `(email, domain, group_tags)` for the throwaway smoke principal.

    The `.test` TLD is RFC 2606 reserved — no deliverability, no collision with
    a real user. A fork overrides the domain via SMOKE_TEST_EMAIL so the
    principal lands in its own namespace rather than ours.
    """
    resolved = email or os.environ.get("SMOKE_TEST_EMAIL") or str(_DEPLOYMENT_IDENTITY["email"])
    if "@" not in resolved:
        raise SmokeConfigError(f"SMOKE_TEST_EMAIL must be an email address, got {resolved!r}")
    domain = resolved.split("@", 1)[1]

    if group_tags is not None:
        tags = group_tags
    else:
        raw = os.environ.get("SMOKE_GROUP_TAGS")
        tags = [t.strip() for t in raw.split(",") if t.strip()] if raw else list(_DEPLOYMENT_IDENTITY["group_tags"])  # type: ignore[arg-type]
    return resolved, domain, tags
