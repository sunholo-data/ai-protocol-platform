"""LOCAL_MODE configuration — the single source of truth for "this backend
is running on a workshop attendee's laptop with no GCP credentials."

LOCAL_MODE swaps in:
- InMemoryFirestoreClient instead of google.cloud.firestore.Client
- A stub auth dependency that accepts only `local-mode-stub-token`
- No-op guards on Vertex AI Search / Cloud Trace / Mailgun init

It is **dev-only** and refuses to coexist with deployment-environment
indicators (K_SERVICE, GAE_ENV, KUBERNETES_SERVICE_HOST) — see
`assert_safe_local_mode()`.

Why a module, not inline `os.environ` checks: callers (fast_api_app.py,
db/firestore.py, auth/firebase.py) all branch on the same flag, and
tests need to override it cleanly. One helper = one place to monkeypatch.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Deployment-environment markers — if any of these are set we are NOT in a
# safe LOCAL_MODE context. Cloud Run / GAE / GKE each set one.
_DEPLOY_ENV_MARKERS: tuple[str, ...] = (
    "K_SERVICE",  # Cloud Run
    "GAE_ENV",  # App Engine
    "KUBERNETES_SERVICE_HOST",  # GKE / generic k8s
)


def is_local_mode() -> bool:
    """Return True when ``LOCAL_MODE=1`` (or ``true``) in the environment.

    The check is intentionally case-insensitive and treats ``1`` / ``true`` /
    ``yes`` / ``on`` as truthy. Everything else (including unset) is False.
    """
    raw = os.environ.get("LOCAL_MODE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_local_mode_persistent() -> bool:
    """When LOCAL_MODE is on, optionally persist the in-memory Firestore to
    ``~/.aitana-local/firestore.json`` on shutdown so state survives a
    ``make dev`` restart. Off by default for purity.
    """
    raw = os.environ.get("LOCAL_MODE_PERSIST", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def assert_safe_local_mode() -> None:
    """Refuse to start if LOCAL_MODE is paired with any deployment marker.

    Raises:
        RuntimeError: When LOCAL_MODE=1 AND any of K_SERVICE, GAE_ENV,
            KUBERNETES_SERVICE_HOST are set. The error message names the
            offending variable so the operator can correct it.

    This is the central safety mitigation for the auth-bypass: a stub
    identity must never reach a real service URL.
    """
    if not is_local_mode():
        return
    offenders = [name for name in _DEPLOY_ENV_MARKERS if os.environ.get(name)]
    if offenders:
        raise RuntimeError(
            "LOCAL_MODE=1 is set together with deployment-environment "
            f"variables: {', '.join(offenders)}. LOCAL_MODE injects a stub "
            "identity and an in-memory store; running it in a deployed "
            "context is a security footgun. Unset LOCAL_MODE for cloud "
            "deployments, or unset the deployment marker(s) for local dev."
        )


def warn_on_session_artifact_pairing() -> None:
    """Emit a WARNING when only one of ``AGENT_ENGINE_ID`` /
    ``ADK_ARTIFACT_BUCKET`` is set.

    Mixed configs strand sessions across restarts: the session's
    ``app:docs_loaded`` list survives but the ``doc:{id}.json`` artifacts
    don't, and the document injector then loads nothing. The orphan-probe
    in adk/callbacks.py self-heals on the next user message, but the
    cleanest answer is parity — set both or neither. LOCAL_MODE always
    means "both in-memory" and is mutually exclusive with either cloud var.
    """
    if is_local_mode():
        cloud_vars = [v for v in ("AGENT_ENGINE_ID", "ADK_ARTIFACT_BUCKET") if os.environ.get(v)]
        if cloud_vars:
            logger.warning(
                "LOCAL_MODE=1 with %s set — cloud session/artifact vars "
                "are ignored in LOCAL_MODE. Unset them to silence this warning.",
                ", ".join(cloud_vars),
            )
        return
    agent_engine = bool(os.environ.get("AGENT_ENGINE_ID"))
    artifact_bucket = bool(os.environ.get("ADK_ARTIFACT_BUCKET"))
    if agent_engine != artifact_bucket:
        only_set = "AGENT_ENGINE_ID" if agent_engine else "ADK_ARTIFACT_BUCKET"
        logger.warning(
            "%s is set but its pair is not. Mixed cloud-sessions + "
            "in-memory-artifacts (or vice versa) strands sessions across "
            "backend restarts. Either set both (cloud) or neither "
            "(in-memory). The orphan-probe in adk/callbacks.py heals this "
            "on the next user message but the warning is the up-front cue.",
            only_set,
        )


def disabled_services() -> list[str]:
    """Return the list of GCP service slugs disabled in LOCAL_MODE.

    Surfaced via ``GET /api/local-mode-status`` so the frontend banner
    can show exactly what's stubbed.
    """
    if not is_local_mode():
        return []
    return [
        "firestore",
        "firebase_auth",
        "vertex_search",
        "cloud_trace",
        "cloud_logging",
        "mailgun",
        "gcs_artifacts",
        "agent_engine_sessions",
    ]
