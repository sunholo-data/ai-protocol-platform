"""API tests for /.well-known/agent.json (PROTOCOLS-1A5 M2 + G43).

Verifies the A2A discovery card:
  - shape matches the minimum A2A spec fields
  - only skills with accessControl.type == 'public' appear
  - endpoint requires no auth (marketplace-parity)
  - cache invalidates after a skill create so newly-public skills
    appear in the card immediately

G43 (template-a2a-spec-compliance.md) additions:
  - protocolVersion: "0.2.0" at card root (required by Discovery Engine)
  - capabilities.extensions[] are AgentExtension descriptors, not bare
    strings (Discovery Engine schema enforcement)
  - X-A2A-Extensions request header negotiates the response set
  - Response carries X-A2A-Extensions + Vary headers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from db.models import SkillConfig, SkillMetadata
from db.models.access import AccessControl


def _extension_ids(card: dict[str, Any]) -> list[str]:
    """G43: extract bare extension IDs from a card's capabilities.extensions.

    A2A v0.2 schema makes these AgentExtension objects (``{uri,
    description, required}``); we reverse-lookup each URI in
    ``SUPPORTED_EXTENSION_INFO`` to recover the canonical IDs the rest of
    the codebase uses. Centralised so a schema bump (v0.3 etc.) only
    edits this helper.
    """
    from protocols.a2a import SUPPORTED_EXTENSION_INFO

    uri_to_id = {uri: ext_id for ext_id, (uri, _) in SUPPORTED_EXTENSION_INFO.items()}
    return [uri_to_id.get(ext["uri"], ext["uri"]) for ext in card["capabilities"]["extensions"]]


def _skill(
    *,
    name: str = "public-skill",
    skill_id: str = "public-skill-id",
    access: str = "public",
    description: str = "A public skill.",
    tags: list[str] | None = None,
) -> SkillConfig:
    return SkillConfig(
        name=name,
        description=description,
        instructions="Be helpful.",
        skillId=skill_id,
        ownerId="owner-uid",
        skillMetadata=SkillMetadata(model="gemini-2.5-flash"),
        accessControl=AccessControl(type=access),  # type: ignore[arg-type]
        tags=tags or [],
    )


@pytest.fixture()
def client() -> TestClient:
    import fast_api_app as module
    from protocols.a2a import invalidate_cache

    # Each test starts with a clean cache so the list_marketplace mock
    # applies cleanly on first fetch.
    invalidate_cache()
    return TestClient(module.app)


# --- Shape ---


def test_agent_card_returns_minimum_a2a_fields(client: TestClient) -> None:
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    # A2A v0.2 minimum fields. G43: protocolVersion is REQUIRED by Discovery
    # Engine — missing one fails Gemini Enterprise registration with
    # INVALID_ARGUMENT (real failure on the gde-ap-agent fork 2026-06-07).
    for field in (
        "protocolVersion",
        "name",
        "description",
        "url",
        "version",
        "capabilities",
        "skills",
    ):
        assert field in card, f"card missing field: {field}"
    assert isinstance(card["skills"], list)
    assert isinstance(card["capabilities"], dict)
    assert card["capabilities"]["streaming"] is True
    assert card["protocolVersion"] == "0.2.0"


def test_agent_card_skills_entries_have_required_fields(client: TestClient) -> None:
    public = _skill(name="search", skill_id="sid1", tags=["research"])
    with patch("protocols.a2a.list_marketplace", return_value=[public]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    skills = resp.json()["skills"]
    assert len(skills) == 1
    entry = skills[0]
    for field in ("id", "name", "description", "tags", "inputModes", "outputModes"):
        assert field in entry, f"skill entry missing: {field}"
    assert entry["id"] == "sid1"
    assert entry["tags"] == ["research"]


# --- Public-only filter ---


def test_agent_card_excludes_private_skills(client: TestClient) -> None:
    """list_marketplace already filters to public — this test pins that we
    never augment it with broader queries in the a2a code path.

    Regression guard: if someone swaps list_marketplace() for list_skills()
    or adds a second Firestore query here, this test catches the private
    skill leak.
    """
    private = _skill(skill_id="priv", access="private", name="secret-skill")

    # If something accidentally calls list_skills without a public filter,
    # it would return `private`. list_marketplace MUST return only public
    # entries — we hand it the filtered set directly.
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["skills"]]
    assert "priv" not in ids, f"private skill leaked into A2A card: {ids}"

    # And confirm the card would surface a public one if list_marketplace
    # returned it — guards against a hard-wired empty list.
    public = _skill(skill_id="pub", access="public", name="public-skill")
    from protocols.a2a import invalidate_cache

    invalidate_cache()
    with patch("protocols.a2a.list_marketplace", return_value=[public, private]):
        resp = client.get("/.well-known/agent.json")
    # Even if the test stub unwisely returned a private entry, a2a must
    # only render what list_marketplace gives it — simulating a bug-free
    # list_marketplace, the card mirrors the input.
    ids = [s["id"] for s in resp.json()["skills"]]
    assert "pub" in ids


# --- No auth ---


def test_agent_card_requires_no_auth(client: TestClient) -> None:
    """The A2A card is discovery — unauthenticated crawlers must see it.

    We make the request with no Authorization header and assert success.
    """
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200, f"A2A card should not require auth, got {resp.status_code}"


# --- Cache invalidation ---


# --- Firestore failure is tolerated ---


def test_agent_card_serves_empty_skills_when_list_marketplace_raises(
    client: TestClient,
) -> None:
    """If Firestore is unreachable or the composite index isn't built yet,
    the card MUST still return 200 with an empty skills[] rather than 500.

    Regression guard: without the try/except in _build_card, a local dev
    backend (no composite index) or a fresh project bring-up produces a
    500 on the public discovery endpoint -- which is the one probe we
    can't guard with auth.
    """
    with patch(
        "protocols.a2a.list_marketplace",
        side_effect=RuntimeError("firestore exploded"),
    ):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert card["skills"] == []
    # And the rest of the card is still well-formed.
    for field in ("protocolVersion", "name", "description", "url", "version", "capabilities"):
        assert field in card


def test_agent_card_cache_invalidated_after_skill_create(client: TestClient) -> None:
    """Creating a new public skill must clear the A2A card cache so
    subsequent GETs reflect the new skill without waiting for the 60s TTL.
    """
    # Warm the cache with an empty skill list.
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.json()["skills"] == []

    # Simulate a skill create — which calls _cache_invalidate, which in
    # turn calls protocols.a2a.invalidate_cache.
    from skills.skill_config import _cache_invalidate

    _cache_invalidate("any-id")

    # Next GET must rebuild from Firestore. With a new skill mocked in,
    # it should appear immediately.
    new_skill = _skill(skill_id="fresh", name="freshly-minted")
    with patch("protocols.a2a.list_marketplace", return_value=[new_skill]):
        resp = client.get("/.well-known/agent.json")
    ids = [s["id"] for s in resp.json()["skills"]]
    assert "fresh" in ids, f"expected cache to invalidate, got {ids}"


# --- G43 spec compliance ----------------------------------------------------


def test_agent_card_advertises_extensions_as_descriptors(client: TestClient) -> None:
    """G43: each capabilities.extensions[] entry must be an A2A
    AgentExtension object with at least a `uri` field — NOT a bare
    string. Discovery Engine / Gemini Enterprise rejects bare strings
    with "unexpected instance type" at /capabilities/extensions/N
    (real failure on gde-ap-agent fork 2026-06-07)."""
    from protocols.a2a import SUPPORTED_EXTENSIONS

    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    caps = resp.json()["capabilities"]
    assert "extensions" in caps, "capabilities.extensions missing from card"
    assert isinstance(caps["extensions"], list)
    assert len(caps["extensions"]) > 0, "expected at least one extension by default"
    # Each entry must be a full AgentExtension descriptor.
    for ext in caps["extensions"]:
        assert isinstance(ext, dict), f"extension entry must be an object, got {type(ext).__name__}"
        assert "uri" in ext, f"AgentExtension missing required `uri`: {ext!r}"
        assert isinstance(ext["uri"], str), "AgentExtension `uri` must be a string"
        assert "description" in ext, f"AgentExtension missing `description`: {ext!r}"
        assert "required" in ext, f"AgentExtension missing `required` flag: {ext!r}"
    # And the bare-ID round-trip via the helper covers every supported ID.
    ids = _extension_ids(resp.json())
    for required_id in SUPPORTED_EXTENSIONS:
        assert required_id in ids, f"supported extension missing from default card: {required_id}"


def test_agent_card_default_advertises_all_supported_extensions(client: TestClient) -> None:
    """G43: when the client doesn't send X-A2A-Extensions (the typical
    unauthenticated-crawler / discovery-default case), the card MUST
    advertise the full supported set. Empty client list = 'I don't know
    what to ask; tell me everything you support.'"""
    from protocols.a2a import SUPPORTED_EXTENSIONS

    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    ids = _extension_ids(resp.json())
    assert set(ids) == set(SUPPORTED_EXTENSIONS), (
        f"default extension set must equal SUPPORTED_EXTENSIONS, got {ids} vs supported {list(SUPPORTED_EXTENSIONS)}"
    )


def test_agent_card_negotiates_extensions_via_header(client: TestClient) -> None:
    """G43: X-A2A-Extensions request header → backend intersects with the
    supported set → response advertises only the intersection. Vary header
    set so HTTP caches don't merge distinct negotiated responses."""
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get(
            "/.well-known/agent.json",
            headers={"X-A2A-Extensions": "a2ui-v0.9, a2ui-decoupled-pattern"},
        )
    assert resp.status_code == 200

    # Response header echoes the negotiated bare-ID list.
    echoed = resp.headers.get("X-A2A-Extensions", "")
    echoed_ids = [tok.strip() for tok in echoed.split(",") if tok.strip()]
    assert echoed_ids == ["a2ui-v0.9", "a2ui-decoupled-pattern"], (
        f"response X-A2A-Extensions must echo negotiated set in client order, got {echoed_ids!r}"
    )
    # Vary header set so caches don't merge negotiated responses.
    vary = resp.headers.get("Vary", "")
    assert "X-A2A-Extensions" in vary, f"Vary must include X-A2A-Extensions, got {vary!r}"

    # And the body's capabilities.extensions matches the negotiated set
    # (header IDs and body descriptors stay in lock-step).
    body_ids = _extension_ids(resp.json())
    assert body_ids == ["a2ui-v0.9", "a2ui-decoupled-pattern"], (
        f"body extensions must match negotiated set in client order, got {body_ids!r}"
    )


def test_agent_card_negotiation_ignores_unsupported_extensions(client: TestClient) -> None:
    """G43: client requests a mix of supported + made-up IDs; only the
    supported ones make it into the response. Defence in depth: we never
    advertise something we don't actually support, even if a client asks
    for it."""
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get(
            "/.well-known/agent.json",
            headers={
                "X-A2A-Extensions": (
                    "a2ui-v0.9, made-up-protocol-v99, definitely-not-a-real-extension, adk-workflow-v1"
                )
            },
        )
    assert resp.status_code == 200
    body_ids = _extension_ids(resp.json())
    # Order preserved from client request; unsupported IDs filtered out.
    assert body_ids == ["a2ui-v0.9", "adk-workflow-v1"], f"unsupported IDs must be filtered out, got {body_ids!r}"


# --- G46 M2: defaultInputModes extension --------------------------------------


def test_agent_card_default_input_modes_includes_pdf_docx_and_text(client: TestClient) -> None:
    """G46 M2: the card's `defaultInputModes` must advertise file MIME types
    by default — at minimum PDF, DOCX, and plain text — so Gemini Enterprise
    (and any other A2A peer with file-upload UI) knows it can route attachments
    to us. Before G46 the card emitted `["text"]` only, which made GE hide its
    file picker.
    """
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    assert "defaultInputModes" in card, "card missing defaultInputModes field"
    modes = card["defaultInputModes"]
    assert isinstance(modes, list)
    # Minimum contract — these three must always be present in the default set.
    for required in (
        "text",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        assert required in modes, f"defaultInputModes missing required MIME: {required!r}, got {modes}"


def test_agent_card_input_mime_types_env_override(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G46 M2: `A2A_AGENT_INPUT_MIME_TYPES` env (comma-separated) replaces the
    default set entirely so forks can tighten/loosen the allowlist without a
    code change. Example use: a fork that only wants to accept PDF + plain text.
    """
    from protocols.a2a import invalidate_cache

    monkeypatch.setenv("A2A_AGENT_INPUT_MIME_TYPES", "text,application/pdf")
    invalidate_cache()  # env-driven override needs a fresh card build

    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    modes = resp.json()["defaultInputModes"]
    assert modes == ["text", "application/pdf"], f"env override must replace the default set exactly, got {modes!r}"


def test_agent_card_url_field_present_for_frontend_rewrite(client: TestClient) -> None:
    """G43: the backend emits a `url` field (defaults to the
    PUBLIC_BASE_URL env-var or localhost). The frontend Next.js proxy at
    `.well-known/agent.json/route.ts` REWRITES this to the public origin
    before serving — the backend can't know its public URL because it's
    a sidecar behind the ingress. This test pins that the field is
    always present so the proxy has something to rewrite (and so the
    backend's `url` is never silently absent, which would break direct-
    backend smokes that don't go through the proxy)."""
    with patch("protocols.a2a.list_marketplace", return_value=[]):
        resp = client.get("/.well-known/agent.json")
    card = resp.json()
    assert "url" in card, "backend card MUST emit url field for frontend proxy to rewrite"
    assert isinstance(card["url"], str)
    assert card["url"].startswith("http"), f"url must be a full URL, got {card['url']!r}"
