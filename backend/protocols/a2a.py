"""A2A agent card at /.well-known/agent.json.

Workshop W4 — A2A: Getting Found
  The whole file is ~200 lines. The business logic is `_skill_to_a2a()`,
  `_build_card()`, and the extension negotiation helpers. The time-bucket
  cache avoids a Firestore read on every crawler hit. Point out `_time_bucket()`
  as the pattern: no scheduler, no background thread, just a rotating lru_cache key.

Unauthenticated discovery endpoint that advertises this platform's
*public* skills to other A2A-compliant agents. Matches marketplace
semantics: if a skill is listed in the public marketplace, it's listed
here too; everything else stays invisible.

Not a full A2A task-handler — that's a follow-up. This is the discovery
surface, cached for 60s so crawlers don't hammer Firestore.

G43 (template-a2a-spec-compliance.md, 2026-06-07): three coupled spec
compliance fixes shipped together so a fresh fork can register with
Gemini Enterprise on first try.

  * `protocolVersion: "0.2.0"` at card root. Discovery Engine's validator
    rejects cards without it — the registration call fails with
    "INVALID_ARGUMENT: required property 'protocolVersion' not found".

  * `capabilities.extensions[]` are A2A `AgentExtension` objects
    `{uri, description, required}`, NOT bare strings. Permissive A2A
    clients tolerate strings; Discovery Engine's strict schema validator
    rejects with "unexpected instance type" at /capabilities/extensions/0.

  * `X-A2A-Extensions` request-header negotiation. Client lists the
    extensions it understands; we intersect with our supported set
    (preserving client order) and echo the negotiated subset on the
    response with `Vary: X-A2A-Extensions` so caches don't merge
    distinct negotiated responses.

The `url` field is still emitted from `PUBLIC_BASE_URL` env (defaulting
to localhost) — but the FRONTEND Next.js proxy at
`frontend/src/app/.well-known/agent.json/route.ts` rewrites it to the
public origin before serving the card. The backend is a sidecar; only
the ingress knows the real URL. See G43 part 2.

See https://github.com/google/a2a for the protocol.
"""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from skills.skill_config import list_marketplace

if TYPE_CHECKING:
    from db.models import SkillConfig

logger = logging.getLogger(__name__)

router = APIRouter()

# Cache TTL in seconds — keeps the card warm for crawlers without
# pinning a stale snapshot for more than a minute. list_marketplace()
# is a Firestore query; 60s is the sweet spot between cost and freshness.
_CACHE_TTL = 60.0

# A2A wire-protocol version this card complies with. Discovery Engine /
# Gemini Enterprise's validator requires this top-level field — a missing
# one fails `agents-cli register-gemini-enterprise --registration-type
# a2a` with "INVALID_ARGUMENT: required property 'protocolVersion' not
# found". Matches the `a2a-v0.2` URI in SUPPORTED_EXTENSION_INFO — keep
# them in sync; the version bump should always touch both.
A2A_PROTOCOL_VERSION = "0.2.0"

# G43: single source of truth for A2A extensions this agent supports.
# The header (X-A2A-Extensions, both directions) uses just the bare IDs;
# the card body needs full AgentExtension descriptors per A2A v0.2 schema.
# Emitting bare strings into the card body fails Discovery Engine
# validation with "unexpected instance type" at /capabilities/extensions/0
# (gde-ap-agent fork hit this 2026-06-07 on a real registration attempt).
#
# Forks adding their own extensions edit THIS dict; the bare-IDs tuple
# below is derived so there's no parallel list to drift.
SUPPORTED_EXTENSION_INFO: dict[str, tuple[str, str]] = {
    "a2ui-v0.9": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/v0.9.md",
        "A2UI v0.9 declarative UI surfaces",
    ),
    "a2ui-basic-catalog-v0.9": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/basic-catalog-v0.9.md",
        "A2UI BasicCatalog component set",
    ),
    "a2ui-inline-pattern": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/inline-pattern.md",
        "A2UI inline-rendered surfaces (in-chat)",
    ),
    "a2ui-decoupled-pattern": (
        "https://github.com/agentic-protocols/a2ui/blob/main/spec/decoupled-pattern.md",
        "A2UI decoupled surfaces (separate pane)",
    ),
    "a2a-v0.2": (
        "https://a2aproject.github.io/A2A/v0.2",
        "A2A protocol v0.2 (this card complies with this version)",
    ),
    "mcp-apps-v1": (
        "https://modelcontextprotocol.io/specification/draft/server/apps",
        "MCP Apps v1 sandboxed iframe artefacts",
    ),
    "adk-workflow-v1": (
        "https://google.github.io/adk-docs/agents/workflow-agents/",
        "ADK workflow agents (SequentialAgent / ParallelAgent / LoopAgent)",
    ),
}

# Canonical bare-ID list. Derived from SUPPORTED_EXTENSION_INFO so adding
# an extension is a one-line dict edit, never a sync between two arrays.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(SUPPORTED_EXTENSION_INFO.keys())


# G46 M2: default MIME types advertised in capabilities.defaultInputModes.
# Before G46 the card emitted ["text"] only, which made Gemini Enterprise
# hide its file picker and prevented any A2A peer from knowing it could
# route .pdf / .docx / etc. attachments through to us. The list mirrors
# the file-extraction interceptor's allowlist in
# `protocols/file_extraction.py:_DEFAULT_INPUT_MIME_TYPES` — keep them
# aligned so a peer that uploads what the card advertises always lands
# in the accept-set on the interceptor side.
_DEFAULT_INPUT_MIMES: tuple[str, ...] = (
    "text",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "message/rfc822",
    "text/csv",
    "text/plain",
)


def _resolve_input_mimes() -> list[str]:
    """Return the MIME list to advertise in defaultInputModes.

    Reads the comma-separated env override ``A2A_AGENT_INPUT_MIME_TYPES``
    when set (lets a fork tighten / loosen the allowlist without a code
    change); otherwise emits the 9-MIME default tuned for the v6
    file-extraction interceptor's accept-set.

    Empty env value (``A2A_AGENT_INPUT_MIME_TYPES=""``) is treated as
    unset — the default is used. This matches the file-extraction
    module's symmetric helper so the two surfaces never drift on the
    "empty string means default" edge case.
    """
    raw = os.environ.get("A2A_AGENT_INPUT_MIME_TYPES", "").strip()
    if not raw:
        return list(_DEFAULT_INPUT_MIMES)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _extension_descriptor(ext_id: str) -> dict[str, Any]:
    """Wrap a supported extension ID as an A2A AgentExtension object.

    Falls back to a synthetic urn:-style URI if the ID is missing from the
    info table — defence in depth so a fork adding a new extension without
    updating SUPPORTED_EXTENSION_INFO still produces a card that passes
    schema validation rather than crashing the endpoint.
    """
    uri, description = SUPPORTED_EXTENSION_INFO.get(
        ext_id,
        (f"urn:sunholo:a2a-extension:{ext_id}", ext_id),
    )
    return {"uri": uri, "description": description, "required": False}


def _parse_client_extensions(header_value: str | None) -> list[str]:
    """Parse the X-A2A-Extensions request header into a list of bare IDs.

    Per the A2A integration guide the header is a comma-separated list
    of bare extension IDs (NOT descriptors — the header doesn't carry
    URI/description, only the card body does).
    """
    if not header_value:
        return []
    return [token.strip() for token in header_value.split(",") if token.strip()]


def _negotiate_extensions(client_ids: list[str]) -> list[str]:
    """Intersect client-requested extension IDs with the platform's
    supported set, preserving the CLIENT's order so client preference
    wins where ours allows.

    Empty client list (= no X-A2A-Extensions header on the request) means
    "I don't know which to ask for; advertise everything you support."
    That's the unauthenticated-crawler / discovery-default case.
    """
    if not client_ids:
        return list(SUPPORTED_EXTENSIONS)
    supported = set(SUPPORTED_EXTENSIONS)
    return [cid for cid in client_ids if cid in supported]


def _skill_to_a2a(skill: SkillConfig) -> dict[str, Any]:
    """Convert a SkillConfig to the A2A skills[] entry shape."""
    return {
        "id": skill.skill_id,
        "name": skill.display_name or skill.name,
        "description": skill.description,
        "tags": list(skill.tags),
        # A2A is modality-flexible; we handle text in and text + A2UI
        # (JSON in fenced blocks) out. Keeping this narrow — extend when
        # we actually start serving audio/image inputs via A2A.
        "inputModes": ["text"],
        "outputModes": ["text"],
    }


# G44: A2A JSON-RPC invocation surface mount point. The card's `url` field
# points HERE so peers know where to POST `message/send` requests. Keep in
# sync with the `app.mount(A2A_INVOCATION_PATH, ...)` call in fast_api_app.py.
A2A_INVOCATION_PATH = "/a2a"


def _build_card_dict(base_url: str) -> dict[str, Any]:
    """Build the A2A card as a dict, advertising the full extension set.

    Used by the ADK-mounted invocation surface (protocols.a2a_invocation),
    which serves a card per-mount without per-request negotiation — the
    ADK A2A surface advertises EVERY supported extension. Negotiation
    happens at the discovery card layer (the route handler below).
    """
    return _build_card(base_url, SUPPORTED_EXTENSIONS)


def _build_card_model(base_url: str) -> Any:
    """Build the A2A card as an ADK `AgentCard` pydantic model.

    Passed to `A2AStarletteApplication(agent_card=...)` so the mounted A2A
    surface advertises the SAME shape as `/.well-known/agent.json`. Pydantic
    runs wire validation at construction time — if our dict's shape is
    wrong we find out at boot, not at a peer's first request.

    Import deferred because `a2a.types` is part of the `a2a-sdk` dep that
    ships with `google-adk`; keeping the import local avoids loading the
    full A2A SDK when only the discovery card is needed.
    """
    from a2a.types import AgentCard

    return AgentCard.model_validate(_build_card_dict(base_url))


def _build_card(base_url: str, ext_ids: tuple[str, ...]) -> dict[str, Any]:
    """Generate the A2A card from the current public skill set.

    If Firestore is unreachable or the composite marketplace index
    hasn't built yet, we serve an empty skills[] rather than 500-ing
    the card: discovery stays working even when the catalogue isn't.

    Args:
        base_url: Public URL the card should advertise. Backend fallback is
            ``localhost:1956``; the frontend Next.js proxy at
            ``.well-known/agent.json/route.ts`` rewrites this to the real
            public origin before serving (the backend is a sidecar and
            can't know its own public URL — G43 part 2).
        ext_ids: Bare extension IDs to advertise on this card. Passed in
            from the route handler after negotiation against the client's
            ``X-A2A-Extensions`` header. Each ID is wrapped as an
            AgentExtension descriptor in capabilities.extensions[].
    """
    try:
        skills = list_marketplace(limit=100)
    except Exception:
        logger.exception("a2a._build_card: list_marketplace failed; serving empty skills")
        skills = []
    return {
        # G43: required by Discovery Engine validator. Missing this field
        # fails Gemini Enterprise registration with INVALID_ARGUMENT.
        "protocolVersion": A2A_PROTOCOL_VERSION,
        # User-visible card identity. Downstream forks override via the
        # A2A_AGENT_NAME / A2A_AGENT_DESCRIPTION env vars (default:
        # upstream Sunholo branding). Mirrors the frontend BRANDING
        # constant's appName + description (kept in sync manually for
        # now — a small follow-up could centralise via a backend
        # BRANDING module).
        "name": os.getenv("A2A_AGENT_NAME", "Sunholo AI Protocol Platform"),
        "description": os.getenv(
            "A2A_AGENT_DESCRIPTION",
            "Open-source AI protocol platform — Skills + AG-UI + A2UI + MCP Apps + A2A on Google ADK.",
        ),
        # iconUrl is an optional A2A v0.2 field consumed by Gemini Enterprise,
        # peer-agent marketplaces, and any A2A client that renders agent
        # avatars.
        #
        # IMPORTANT: agents-cli only copies iconUrl onto the registered
        # Agent resource when it's same-host as card.url. Cross-host icons
        # (e.g. a logo on a marketing site) are SILENTLY REPLACED with
        # Google's default smart_toy placeholder in the GE Console. So we
        # default to a path on the same Cloud Run host that serves the
        # card itself; the SVG sits in frontend/public/images/logo/ and
        # is reachable through the Next.js public/ pipeline.
        #
        # Two env-var override knobs:
        #   - A2A_AGENT_ICON_URL: absolute URL override (most flexible).
        #     Cross-host URLs are accepted by A2A clients but get replaced
        #     by GE Console's default fallback — only use this when your
        #     consumer doesn't care about the GE Console rendering.
        #   - A2A_AGENT_ICON_PATH: path override on the same host as
        #     card.url. Same-host means agents-cli preserves the icon
        #     onto agent.icon.uri. Defaults to /images/logo/sunholo-logo.svg
        #     which ships in the template — forks swap the asset OR
        #     override the path.
        "iconUrl": os.getenv(
            "A2A_AGENT_ICON_URL",
            f"{base_url.rstrip('/')}{os.getenv('A2A_AGENT_ICON_PATH', '/images/logo/sunholo-logo.svg')}",
        ),
        "url": f"{base_url.rstrip('/')}{A2A_INVOCATION_PATH}",
        "version": "6.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            # G43: AgentExtension descriptor objects, NOT bare strings.
            # Bare strings fail Discovery Engine validation with
            # "unexpected instance type" at /capabilities/extensions/N.
            "extensions": [_extension_descriptor(eid) for eid in ext_ids],
        },
        # G46 M2: defaultInputModes advertises the MIME types the agent
        # accepts via A2A FilePart. Empty / unset env → 9-MIME default
        # (text + PDF + DOCX/XLSX/PPTX + ODT + EML + CSV + plain text);
        # override via A2A_AGENT_INPUT_MIME_TYPES (comma-separated).
        # Without the file MIMEs, Gemini Enterprise's workspace UI hides
        # its file picker so users can't upload anything for this agent.
        "defaultInputModes": _resolve_input_mimes(),
        "defaultOutputModes": ["text"],
        "skills": [_skill_to_a2a(s) for s in skills],
    }


# --- Cache ---
# lru_cache on a timestamped key: mod the timestamp to _CACHE_TTL so the
# key rotates once per TTL window, giving us time-bounded caching without
# a scheduler. Call sites pass `_time_bucket()` as the cache key. The
# negotiated extension tuple is also part of the cache key so different
# clients can get different cards from the same TTL window without
# poisoning each other.


def _time_bucket() -> int:
    return int(time.time() // _CACHE_TTL)


@lru_cache(maxsize=16)
def _cached_card(base_url: str, bucket: int, ext_ids: tuple[str, ...]) -> dict[str, Any]:
    # `bucket` is part of the cache key only — it forces cache invalidation
    # when the 60s window rolls over. It isn't used inside the body.
    del bucket
    return _build_card(base_url, ext_ids)


def invalidate_cache() -> None:
    """Force the next /.well-known/agent.json hit to rebuild from Firestore.

    Called by skill CRUD routes after create/update/delete so the card
    reflects the new public skill set without waiting for the 60s TTL.
    """
    _cached_card.cache_clear()


# --- Route ---


@router.get("/.well-known/agent.json")
def agent_card(request: Request) -> JSONResponse:
    """A2A agent card. Unauthenticated — advertises public skills only.

    Private / domain / specific / tagged skills never appear here: the
    `list_marketplace()` query filters on `accessControl.type == "public"`.

    G43: capability-negotiates against the client's ``X-A2A-Extensions``
    request header. Empty/absent header → advertise the full
    ``SUPPORTED_EXTENSIONS`` set (discovery default). The response carries
    the negotiated set on its own ``X-A2A-Extensions`` header plus
    ``Vary: X-A2A-Extensions`` so HTTP caches don't merge distinct
    negotiated responses.
    """
    client_ids = _parse_client_extensions(request.headers.get("X-A2A-Extensions"))
    negotiated = _negotiate_extensions(client_ids)
    base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:1956")
    card = _cached_card(base_url, _time_bucket(), tuple(negotiated))
    return JSONResponse(
        content=card,
        headers={
            "X-A2A-Extensions": ", ".join(negotiated),
            "Vary": "X-A2A-Extensions",
        },
    )
