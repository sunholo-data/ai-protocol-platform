#!/usr/bin/env python3
"""Bootstrap a Vertex AI Agent Engine for v6 sessions + memory.

The v6 backend uses Agent Engine for session and memory persistence (pay-per-use)
but is deployed on Cloud Run, not on Agent Engine itself. This script creates a
minimal Agent Engine resource and prints its resource ID, which should be stored
in Secret Manager as AGENT_ENGINE_ID.

Idempotent: if an Agent Engine with the target display name already exists, its
resource ID is printed and no new resource is created.

Usage:
    export GOOGLE_CLOUD_PROJECT=your-project-id
    export GOOGLE_CLOUD_LOCATION=europe-west1
    export AGENT_ENGINE_STAGING_BUCKET=gs://dev-platform-logs  # optional
    uv run python backend/scripts/bootstrap_agent_engine.py
"""

from __future__ import annotations

import argparse
import os
import sys

# Display name used to de-duplicate the Agent Engine across re-runs.
DEFAULT_DISPLAY_NAME = "platform"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _numeric_id(resource_name: str) -> str:
    """Extract trailing numeric ID from a full Agent Engine resource name.

    ADK's `VertexAiSessionService(agent_engine_id=...)` expects just the numeric
    suffix (e.g. `6224370509212024832`), NOT the full resource path. Passing the
    full path doubles the `reasoningEngines/` prefix in generated URLs → 404.
    """
    return resource_name.rstrip("/").rsplit("/", 1)[-1] if "/" in resource_name else resource_name


def bootstrap(display_name: str, dry_run: bool) -> str:
    """Create or find the Agent Engine and return its numeric resource ID."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west1")
    if not project:
        raise SystemExit("GOOGLE_CLOUD_PROJECT must be set")

    _log(f"Project:      {project}")
    _log(f"Location:     {location}")
    _log(f"Display name: {display_name}")

    if dry_run:
        _log("[dry-run] would call vertexai.init + agent_engines.list/create")
        return "<numeric-id>"

    import vertexai
    from vertexai import agent_engines

    staging = os.environ.get("AGENT_ENGINE_STAGING_BUCKET")
    init_kwargs = {"project": project, "location": location}
    if staging:
        init_kwargs["staging_bucket"] = staging
        _log(f"Staging:      {staging}")
    vertexai.init(**init_kwargs)

    # Idempotency: find existing engine by display name.
    for existing in agent_engines.list():
        if getattr(existing, "display_name", None) == display_name:
            resource_name = existing.resource_name
            _log(f"Found existing Agent Engine: {resource_name}")
            return _numeric_id(resource_name)

    _log("No existing Agent Engine matched display name — creating new one.")

    # Minimal agent_engine payload. We only need the resource to anchor
    # VertexAiSessionService; the Cloud Run backend holds the real agent logic.
    try:
        remote = agent_engines.create(
            display_name=display_name,
            description="Aitana v6 session + memory anchor (backend runs on Cloud Run).",
        )
    except TypeError:
        # Older SDK surfaces require an agent_engine argument; fall back to a no-op wrapper.
        class _NoOpEngine:
            def query(self, **_: object) -> dict[str, str]:
                return {"response": "noop"}

        remote = agent_engines.create(
            agent_engine=_NoOpEngine(),
            display_name=display_name,
            description="Aitana v6 session + memory anchor (backend runs on Cloud Run).",
        )

    resource_name = remote.resource_name
    _log(f"Created Agent Engine: {resource_name}")
    return _numeric_id(resource_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--dry-run", action="store_true", help="Print plan without calling Vertex AI")
    args = parser.parse_args()

    numeric_id = bootstrap(args.display_name, args.dry_run)
    # stdout: numeric ID only — callers pipe into gcloud secrets versions add.
    # ADK's VertexAiSessionService requires the trailing numeric ID, not the
    # full resource name. See _numeric_id() docstring.
    print(numeric_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
