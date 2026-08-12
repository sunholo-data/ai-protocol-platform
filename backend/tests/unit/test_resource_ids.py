"""Unit tests for tools/resource_ids.py.

G15 (template-fork-ergonomics.md): bare resource ids in SKILL.md
`toolConfigs` are expanded to full GCP resource paths before being
handed to Vertex SDKs. Forks that explicitly pass full paths must NOT
have those paths rewritten.
"""

from __future__ import annotations

import pytest

from tools.resource_ids import resolve_resource_id

# === bare-id expansion (the main fix) ===


def test_expands_bare_datastore_id_using_env(monkeypatch):
    monkeypatch.delenv("VERTEX_AI_SEARCH_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_AI_SEARCH_LOCATION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")

    out = resolve_resource_id("vertex_datastore", "ds-ap-vendors")

    assert out == "projects/my-project/locations/europe-west1/collections/default_collection/dataStores/ds-ap-vendors"


def test_search_specific_env_takes_precedence_over_compute_project(monkeypatch):
    """Datastores live in a dedicated search-infra project (aitana-ai-search),
    not the compute project. VERTEX_AI_SEARCH_* must win over GOOGLE_CLOUD_*."""
    monkeypatch.setenv("VERTEX_AI_SEARCH_PROJECT", "aitana-ai-search")
    monkeypatch.setenv("VERTEX_AI_SEARCH_LOCATION", "eu")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "your-project-id")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")

    out = resolve_resource_id("vertex_datastore", "aitana4")

    assert out == "projects/aitana-ai-search/locations/eu/collections/default_collection/dataStores/aitana4"


def test_falls_back_to_global_location_when_GOOGLE_CLOUD_LOCATION_unset(monkeypatch):
    monkeypatch.delenv("VERTEX_AI_SEARCH_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_AI_SEARCH_LOCATION", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    out = resolve_resource_id("vertex_datastore", "ds-foo")

    assert "/locations/global/" in out


# === pass-through (don't rewrite explicit full paths) ===


def test_passes_full_resource_path_through_unchanged(monkeypatch):
    """Forks that explicitly point at a different project/location must
    keep that explicit value — the expansion is a SHORTHAND, not a
    rewrite-everything contract."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")

    explicit = "projects/other-project/locations/us-central1/collections/default_collection/dataStores/shared-vendors"

    assert resolve_resource_id("vertex_datastore", explicit) == explicit


def test_anything_with_a_slash_is_treated_as_already_expanded(monkeypatch):
    """The "is this a bare id?" check is "contains no slash". Anything
    with a slash is assumed to be a path the author meant verbatim."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")

    # Even a partial path passes through — we don't try to be clever.
    partial = "dataStores/ds-foo"
    assert resolve_resource_id("vertex_datastore", partial) == partial


def test_empty_value_passes_through(monkeypatch):
    """Empty value is a tolerated edge case (caller handles the
    'no datastore configured' branch); we don't synthesize one."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    assert resolve_resource_id("vertex_datastore", "") == ""


# === error paths ===


def test_raises_when_project_env_missing(monkeypatch):
    """Bare id + no project = unrecoverable. Raise loudly with a
    fix-it-yourself error message rather than silently emitting a
    malformed `projects/` path Vertex will reject 30 seconds later."""
    monkeypatch.delenv("VERTEX_AI_SEARCH_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        resolve_resource_id("vertex_datastore", "ds-foo")


def test_raises_on_unknown_kind(monkeypatch):
    """Defense-in-depth: a future caller that types `kind="bucket"`
    against the current Literal["vertex_datastore"] gets a runtime
    ValueError as well as a static-typecheck error."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")

    with pytest.raises(ValueError, match="Unknown resource kind"):
        # Intentionally mis-typed; suppress the type-check for the test.
        resolve_resource_id("gcs_bucket", "demo")  # type: ignore[arg-type]
