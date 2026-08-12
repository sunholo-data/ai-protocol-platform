"""Bare-id → full-resource-path expansion for SKILL.md `toolConfigs`.

G15 (template-fork-ergonomics.md): a SKILL.md author writes the natural
shorthand form:

    toolConfigs:
      ai_search:
        datastore_id: ds-ap-vendors           # bare id

…and the agent factory expands it to the full resource path Vertex
expects:

    projects/<GOOGLE_CLOUD_PROJECT>/locations/<region>/collections/default_collection/dataStores/ds-ap-vendors

before handing to `VertexAiSearchTool`. Without this indirection, the
runtime forwarded the bare string and Vertex rejected with a generic
`400 INVALID_ARGUMENT: Invalid Vertex AI datastore resource name` —
the gde-ap-agent fork burned a sprint hour on this exact error.

The indirection is centralized here so future resource-kind expansions
(reasoning engines, agent-engine resources, fork-defined custom ids)
have one obvious place to land.
"""

from __future__ import annotations

import os
from typing import Literal

# Currently supported resource kinds. Extend the Literal + the match block
# in `resolve_resource_id` when adding a new kind.
ResourceKind = Literal["vertex_datastore"]


def _project_and_region() -> tuple[str, str]:
    """Resolve the project + location a bare Vertex datastore id lives in.

    Vertex AI Search datastores usually live in a **dedicated search-infra
    project** that is NOT the compute project ADK runs in (Aitana:
    `aitana-ai-search` / `eu`, distinct from the `your-project-id-*` compute
    projects). So prefer the search-specific env vars and only fall back to the
    generic ADK `GOOGLE_CLOUD_*` vars for single-project setups:

      - project:  ``VERTEX_AI_SEARCH_PROJECT``  → ``GOOGLE_CLOUD_PROJECT``
      - location: ``VERTEX_AI_SEARCH_LOCATION`` → ``GOOGLE_CLOUD_LOCATION`` → ``global``

    Raises RuntimeError if no project can be resolved — the bare id was passed
    intentionally and we have no way to expand it.
    """
    project = (
        os.environ.get("VERTEX_AI_SEARCH_PROJECT", "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    )
    if not project:
        raise RuntimeError(
            "No project to expand a bare datastore id. Set VERTEX_AI_SEARCH_PROJECT "
            "(the datastore's project) or GOOGLE_CLOUD_PROJECT, or pass the full "
            "resource path in the SKILL.md toolConfigs entry."
        )
    region = (
        os.environ.get("VERTEX_AI_SEARCH_LOCATION", "").strip()
        or os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
        or "global"
    )
    return project, region


def resolve_resource_id(kind: ResourceKind, value: str) -> str:
    """Return the full GCP resource path for a SKILL.md-declared id.

    - **Already-full paths pass through unchanged** so a fork that wants
      to point at a different project/location keeps the explicit form.
    - **Bare ids** (no slashes) get expanded using the host project +
      region from env.

    Args:
        kind: Which kind of resource. Currently only `"vertex_datastore"`.
        value: Either a bare id (e.g. `"ds-ap-vendors"`) or a full
            resource path (e.g. `"projects/foo/locations/eu/…"`).

    Returns:
        The full resource path Vertex expects.
    """
    if not value:
        return value
    # Already-full path? Pass through.
    if "/" in value:
        return value
    if kind == "vertex_datastore":
        project, region = _project_and_region()
        return f"projects/{project}/locations/{region}/collections/default_collection/dataStores/{value}"
    # Exhaustiveness check — if a new kind is added to ResourceKind but
    # not handled here, mypy/pyright catch it AND we raise at runtime.
    raise ValueError(f"Unknown resource kind: {kind!r}")
