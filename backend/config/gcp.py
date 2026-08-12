"""GCP project resolution.

Centralises the env-var + ADC fallback chain that was duplicated across
``app.py``, ``fast_api_app.py``, ``db/firestore.py``, and ``adk/session.py``.

Lookup order:
    1. ``GOOGLE_CLOUD_PROJECT`` env var
    2. ``GCP_PROJECT`` env var (legacy v5 name)
    3. ``google.auth.default()`` (Application Default Credentials)
"""

from __future__ import annotations

import logging
import os

import google.auth
import google.auth.credentials
import google.auth.exceptions

_log = logging.getLogger(__name__)

# API-key env vars that, in Vertex mode, poison the genai client.
_API_KEY_VARS = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY")


def neutralize_api_key_in_vertex_mode() -> list[str]:
    """Self-heal a deploy that sets an API key alongside Vertex mode.

    When ``GOOGLE_GENAI_USE_VERTEXAI=true``, the google-genai client attaches any
    ``GOOGLE_API_KEY`` / ``GEMINI_API_KEY`` / ``GOOGLE_GENAI_API_KEY`` in the env
    to Vertex calls, and Vertex Sessions/Memory reject API-key auth with a 401
    ``CREDENTIALS_MISSING`` — which silently breaks *every* chat turn. A deploy
    that mounts one of these (e.g. via a shared secret-env list) would otherwise
    take the whole service down.

    Pop the offending vars — but only in Vertex mode — so the app is robust to
    the misconfiguration regardless of what the deploy injects. MUST be called
    before any genai client is created (i.e. at import of the app entrypoint).
    Logs loudly so the deploy env still gets cleaned up at the source. Returns
    the names it popped (for tests / callers that want to log a summary).
    """
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() != "true":
        return []
    leaked = [v for v in _API_KEY_VARS if os.getenv(v)]
    for v in leaked:
        os.environ.pop(v, None)
    if leaked:
        _log.error(
            "STARTUP: unset %s because GOOGLE_GENAI_USE_VERTEXAI=true — an API key "
            "in env makes Vertex Sessions/Memory reject auth with 401 "
            "CREDENTIALS_MISSING and breaks every chat turn. Auto-corrected so the "
            "service works; REMOVE these from the deployed env — they must not be "
            "set in Vertex mode.",
            ", ".join(leaked),
        )
    return leaked


def resolve_gcp_credentials() -> tuple[google.auth.credentials.Credentials, str | None] | None:
    """Return ``(credentials, adc_project)`` or ``None`` when ADC is unavailable.

    Used by callers that need to inspect the credentials object itself
    (e.g. the startup probe that checks ``credentials.quota_project_id``).
    """
    try:
        creds, adc_project = google.auth.default()
        return creds, adc_project
    except google.auth.exceptions.DefaultCredentialsError:
        return None


def resolve_gcp_project() -> str | None:
    """Return the resolved GCP project ID, or None if unavailable."""
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if project:
        return project
    resolved = resolve_gcp_credentials()
    return resolved[1] if resolved else None


def require_gcp_project() -> str:
    """Same as :func:`resolve_gcp_project` but raises when unavailable."""
    project = resolve_gcp_project()
    if not project:
        raise RuntimeError(
            "No GCP project available: set GOOGLE_CLOUD_PROJECT, GCP_PROJECT, "
            "or run with ADC (`gcloud auth application-default login`)."
        )
    return project


#: Sentinel used by ``app.py`` when no project resolves (CI import path). Kept
#: here so the guard and the fallback cannot drift apart.
PLACEHOLDER_PROJECT = "unset-project"


class ProjectGuardError(RuntimeError):
    """Raised when the resolved GCP project is provably wrong for this deployment."""


def check_startup_project(*, local_mode: bool) -> str:
    """Validate the resolved GCP project at boot. Returns it, or raises.

    Replaces a guard that was both **brand-anchored and fail-open** (v6.19.0,
    AIPLA #42): it compared the resolved project against a hardcoded
    ``your-project-id`` prefix and only logged a ``STARTUP WARNING``. That is
    backwards on both counts — it fired on every correctly-configured fork, and
    stayed quiet when the project was genuinely wrong, which is the case it
    existed to catch. A guard that warns when correct and is silent when wrong
    is worse than no guard.

    The replacement derives its expectation instead of baking one in:

    * ``PLATFORM_EXPECTED_PROJECT`` set  -> must match exactly, else refuse to boot.
      This is the real protection for the documented shell-shadow gotcha, where
      a developer's ``GCP_PROJECT`` points at some other project entirely and
      the app cheerfully reads and writes there.
    * ``PLATFORM_EXPECTED_PROJECT`` unset -> nothing to compare against, so make
      no claim. We only require that *some* project resolved.
    * No project at all, outside LOCAL_MODE -> refuse to boot. Firestore, GCS and
      ADK would all silently target the wrong place.

    LOCAL_MODE is exempt throughout: it has no GCP backing by design.
    """
    resolved = resolve_gcp_project()

    if local_mode:
        return resolved or "(local-mode)"

    if not resolved:
        raise ProjectGuardError(
            "No GCP project resolved at startup. Firestore, GCS and ADK would all "
            "target the wrong place. Set GOOGLE_CLOUD_PROJECT (or GCP_PROJECT), or "
            "run with ADC (`gcloud auth application-default login`). "
            "Set LOCAL_MODE=1 to run without GCP."
        )

    # ``app.py`` sets this literal placeholder when nothing resolves, so CI can
    # import the module tree without GCP. Reaching a real boot with it still set
    # means the deployment never configured a project — treat it as unset rather
    # than letting "unset-project" look like a legitimate project name.
    if resolved == PLACEHOLDER_PROJECT:
        raise ProjectGuardError(
            f"GCP project is the placeholder {PLACEHOLDER_PROJECT!r}, which means none was "
            "configured. Set GOOGLE_CLOUD_PROJECT (or PLATFORM_DEFAULT_PROJECT) to the real "
            "project, or set LOCAL_MODE=1 to run without GCP."
        )

    expected = os.getenv("PLATFORM_EXPECTED_PROJECT", "").strip()
    if expected and resolved != expected:
        raise ProjectGuardError(
            f"GCP project mismatch: resolved {resolved!r} but PLATFORM_EXPECTED_PROJECT "
            f"is {expected!r}. Refusing to start rather than read/write the wrong "
            f"project. Fix GOOGLE_CLOUD_PROJECT / GCP_PROJECT, or update "
            f"PLATFORM_EXPECTED_PROJECT if this deployment really did move."
        )

    return resolved
