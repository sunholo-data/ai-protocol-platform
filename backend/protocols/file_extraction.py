"""A2A FilePart extraction interceptor (G46 M1).

Mounted via `A2aAgentExecutorConfig(execute_interceptors=[...])` on the
A2A executor in `protocols.a2a_invocation`. Runs as a `before_agent`
hook so it sees the incoming `RequestContext` before ADK's runner kicks
off.

Why we need this:
  ADK's `convert_a2a_part_to_genai_part` *already* converts A2A FileParts
  into native Gemini multimodal `inline_data` / `file_data` Parts. The
  model would receive the file directly. But our orchestrator pipeline
  is built on the convention where documents are looked up via
  `state["document_ids"]` and a doc-loader callback fetches parsed blocks
  + writes them as `doc:{id}.json` artifacts. The orchestrator's
  instructions reference *that* path — "use the parsed invoice fields
  from session state" — not the raw multimodal input.

  Without this interceptor, GE-routed FileParts would land in Gemini as
  raw bytes and the orchestrator would either ignore them (instructions
  don't tell it to extract from inline data) or treat them as
  unstructured context — defeating the whole document-pipeline path.

What this interceptor does:
  1. Walks `context.message.parts` for FileParts
  2. For each one:
     - Validates MIME type (allowlist), size (≤ A2A_FILE_MAX_BYTES), URI
       scheme (https/gs)
     - For `FileWithBytes`: base64-decode → save as `doc:{id}.json`
       artifact directly (skip the full upload+parse pipeline; v1 keeps
       things simple and ephemeral session-scoped)
     - For `FileWithUri`: register the URI under a minted doc_id; the
       loader fetches lazily
  3. Strips the FileParts from `context.message.parts` so ADK's native
     converter doesn't ALSO send them to Gemini
  4. Writes the minted `document_ids` to the A2A session state

  Result: the existing doc-loader callback sees a populated
  `state["document_ids"]` and behaves identically to an AG-UI upload.

Configuration (env vars):
  ENABLE_A2A_FILE_INPUT (default false) — gates the whole interceptor
  A2A_FILE_MAX_BYTES (default 26_214_400 = 25 MB) — per-file decoded size
  A2A_AGENT_INPUT_MIME_TYPES (used in protocols/a2a.py for card; this
    module reads the same list to know what to accept)

CRITICAL: `force_new_version=True` MUST be set on `A2aAgentExecutor` for
this interceptor to fire. Without it, ADK picks the LEGACY impl path
which only calls interceptors when the peer sends a "new-version" hint
in the X-A2A-Extensions header. Gemini Enterprise doesn't send that hint
(verified live 2026-06-08 on the gde-ap-agent fork), so without the flag
every FilePart silently drops. See test_file_extraction.py for the
regression guard.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from a2a.server.agent_execution.context import RequestContext
    from google.adk.runners import Runner

# Default MIME allowlist — matches the card's `defaultInputModes` (see
# protocols.a2a). Keep in sync if either side changes.
_DEFAULT_INPUT_MIME_TYPES: tuple[str, ...] = (
    "text",  # text/plain implicitly; A2A spec uses bare "text"
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "message/rfc822",  # .eml
    "text/csv",
    "text/plain",
)

_DEFAULT_FILE_MAX_BYTES = 26_214_400  # 25 MiB decoded
_ALLOWED_URI_SCHEMES: tuple[str, ...] = ("https", "gs")

# Session-state keys the interceptor writes. Reads the existing
# document_ids list (set by AG-UI path or prior A2A turns) so we append
# rather than clobber.
_STATE_DOCUMENT_IDS = "document_ids"
# Mirrors `_STATE_DOCS_LOADED` in `adk.callbacks`. The doc-loader's
# `to_load` filter is `[d for d in document_ids if d not in loaded_set]`,
# so adding our minted A2A doc_ids here makes the loader treat them as
# already-materialised and skip the Firestore lookup that would
# otherwise fail (A2A artifacts are session-scoped, not Firestore-backed
# like AG-UI uploads).
_STATE_DOCS_LOADED = "app:docs_loaded"


def _allowed_mime_types() -> set[str]:
    """Parse the configured MIME allowlist from env or defaults."""
    override = os.environ.get("A2A_AGENT_INPUT_MIME_TYPES", "")
    if override:
        return {m.strip() for m in override.split(",") if m.strip()}
    return set(_DEFAULT_INPUT_MIME_TYPES)


def _max_file_bytes() -> int:
    raw = os.environ.get("A2A_FILE_MAX_BYTES", "")
    if not raw:
        return _DEFAULT_FILE_MAX_BYTES
    try:
        return int(raw)
    except ValueError:
        logger.warning("A2A_FILE_MAX_BYTES=%r is not an int; using default", raw)
        return _DEFAULT_FILE_MAX_BYTES


def _validate_file_with_bytes(file: Any, mime_allowlist: set[str], max_bytes: int) -> str | None:
    """Return an error message if invalid, None if OK."""
    mime = file.mime_type or "application/octet-stream"
    if mime not in mime_allowlist:
        return f"MIME type {mime!r} is not in the allowed set"
    # base64 ratio: 4 chars per 3 input bytes; decoded size ~= len*3/4
    encoded_len = len(file.bytes or "")
    decoded_estimate = (encoded_len * 3) // 4
    if decoded_estimate > max_bytes:
        return f"File size {decoded_estimate} bytes exceeds the {max_bytes}-byte limit"
    return None


def _validate_file_with_uri(file: Any, mime_allowlist: set[str]) -> str | None:
    """Return an error message if invalid, None if OK."""
    if not file.uri:
        return "FileWithUri has empty URI"
    scheme = file.uri.split(":", 1)[0].lower() if ":" in file.uri else ""
    if scheme not in _ALLOWED_URI_SCHEMES:
        return f"URI scheme {scheme!r} is not allowed (use https or gs)"
    # MIME on FileWithUri is advisory — we validate only if present so a
    # peer that omits it doesn't get blocked. The fetcher checks the real
    # content-type when it loads.
    if file.mime_type and file.mime_type not in mime_allowlist:
        return f"MIME type {file.mime_type!r} is not in the allowed set"
    return None


async def _save_inline_bytes_as_artifact(
    *,
    runner: Runner,
    app_name: str,
    user_id: str,
    session_id: str,
    doc_id: str,
    decoded: bytes,
    mime_type: str,
    display_name: str | None,
) -> None:
    """Save a FileWithBytes payload as a `doc:{id}.json` session artifact.

    Format mirrors what a typical doc-loader produces for AG-UI uploads
    (an inline_data Blob carrying JSON-encoded blocks) — except for the
    A2A path v1 we shortcut to a single block of raw text/file_data so
    the model sees the content directly without round-tripping through
    ailang-parse + Firestore. Future versions can route through the full
    upload pipeline if forks need persistence / citation.

    Awaited (not fire-and-forget) so the artifact is observable before
    the agent runs — important because the doc-loader callback fires
    within microseconds of this return.
    """
    from google.genai.types import Blob, Part

    block = {
        "kind": "a2a-inline-file",
        "displayName": display_name or "file",
        "mimeType": mime_type,
        "bytesBase64": base64.b64encode(decoded).decode("ascii"),
    }
    artifact = Part(
        inline_data=Blob(
            data=json.dumps([block]).encode("utf-8"),
            mime_type="application/json",
        )
    )
    # ADK's artifact service stores per (app_name, user_id, session_id, filename)
    await runner.artifact_service.save_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=f"doc:{doc_id}.json",
        artifact=artifact,
    )


async def _save_uri_pointer_as_artifact(
    *,
    runner: Runner,
    app_name: str,
    user_id: str,
    session_id: str,
    doc_id: str,
    uri: str,
    mime_type: str | None,
    display_name: str | None,
) -> None:
    """Save a FileWithUri pointer as a `doc:{id}.json` session artifact.

    The pointer carries the URI; the existing doc-loader path knows
    how to follow gs:// and https:// references during turn execution.
    """
    from google.genai.types import Blob, Part

    block = {
        "kind": "a2a-uri-file",
        "displayName": display_name or "file",
        "mimeType": mime_type,
        "uri": uri,
    }
    artifact = Part(
        inline_data=Blob(
            data=json.dumps([block]).encode("utf-8"),
            mime_type="application/json",
        )
    )
    await runner.artifact_service.save_artifact(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        filename=f"doc:{doc_id}.json",
        artifact=artifact,
    )


def _is_enabled() -> bool:
    return os.environ.get("ENABLE_A2A_FILE_INPUT", "false").lower() in ("true", "1", "yes")


def _derive_user_id(context: Any) -> str:
    """Mirror ADK's `_get_user_id` in `a2a/converters/request_converter.py`.

    ADK uses `call_context.user.user_name` when auth populates it, else
    falls back to `f"A2A_USER_{context.context_id}"`. We MUST match this
    derivation when injecting state because ADK's downstream session
    lookup will use the same key. Earlier versions hardcoded
    `"a2a-public-peer"` which caused a session-ownership ValueError when
    Vertex's session_service did the user_id check.
    """
    call_ctx = getattr(context, "call_context", None)
    if call_ctx is not None:
        user = getattr(call_ctx, "user", None)
        if user is not None:
            name = getattr(user, "user_name", None)
            if name:
                return name
    return f"A2A_USER_{context.context_id}"


def make_file_extraction_interceptor(runner: Runner, *, app_name: str, user_id: str | None = None) -> Any:
    """Build an `ExecuteInterceptor` that extracts FileParts before the agent runs.

    Closure captures the runner (for artifact_service + session_service access).
    The `user_id` parameter is kept for API back-compat but the actual
    user_id used for session/artifact writes is derived per-request via
    `_derive_user_id` so it matches what ADK's request_converter does
    downstream. Passing a fixed `user_id` here would cause the session
    Vertex lookup to fail with "does not belong to user" (caught live
    2026-06-08T04:58 against the production Vertex session_service).

    Returns an `ExecuteInterceptor` dataclass instance ready to plug into
    `A2aAgentExecutorConfig(execute_interceptors=[...])`. Disabled at
    invocation time when `ENABLE_A2A_FILE_INPUT` is not truthy — turns
    the interceptor into a pure pass-through so flag-off behaviour is
    byte-identical to no interceptor at all.
    """
    _ = user_id  # retained for API back-compat; actual id derived per-request
    from google.adk.a2a.executor.config import ExecuteInterceptor

    async def _before_agent(context: RequestContext) -> RequestContext:
        if not _is_enabled():
            return context

        if not context.message or not getattr(context.message, "parts", None):
            return context

        # Walk parts, dispatch on root type.
        from a2a.types import FilePart, FileWithBytes, FileWithUri

        mime_allowlist = _allowed_mime_types()
        max_bytes = _max_file_bytes()
        new_parts: list[Any] = []
        new_doc_ids: list[str] = []
        rejected: list[tuple[str, str]] = []  # (filename, reason)
        session_id = context.context_id or ""
        # Derive the user_id ADK will use downstream so our session/artifact
        # writes land under the same key it looks up.
        request_user_id = _derive_user_id(context)

        for part in context.message.parts:
            root = getattr(part, "root", part)
            if not isinstance(root, FilePart):
                new_parts.append(part)
                continue

            # FilePart — validate, persist as artifact, mint doc_id
            file = root.file
            display_name = getattr(file, "name", None)

            if isinstance(file, FileWithBytes):
                err = _validate_file_with_bytes(file, mime_allowlist, max_bytes)
                if err:
                    rejected.append((display_name or "file", err))
                    logger.warning("a2a file rejected: %s — %s", display_name, err)
                    continue
                try:
                    decoded = base64.b64decode(file.bytes)
                except Exception as exc:
                    rejected.append((display_name or "file", f"base64 decode failed: {exc}"))
                    continue

                doc_id = str(uuid.uuid4())
                await _save_inline_bytes_as_artifact(
                    runner=runner,
                    app_name=app_name,
                    user_id=request_user_id,
                    session_id=session_id,
                    doc_id=doc_id,
                    decoded=decoded,
                    mime_type=file.mime_type or "application/octet-stream",
                    display_name=display_name,
                )
                new_doc_ids.append(doc_id)
                logger.info(
                    "a2a file accepted (bytes): name=%s mime=%s decoded=%d doc_id=%s",
                    display_name,
                    file.mime_type,
                    len(decoded),
                    doc_id,
                )
                # Strip — model gets it via doc_loader, not native multimodal
                continue

            if isinstance(file, FileWithUri):
                err = _validate_file_with_uri(file, mime_allowlist)
                if err:
                    rejected.append((display_name or "uri", err))
                    logger.warning("a2a uri rejected: %s — %s", file.uri, err)
                    continue

                doc_id = str(uuid.uuid4())
                await _save_uri_pointer_as_artifact(
                    runner=runner,
                    app_name=app_name,
                    user_id=request_user_id,
                    session_id=session_id,
                    doc_id=doc_id,
                    uri=file.uri,
                    mime_type=file.mime_type,
                    display_name=display_name,
                )
                new_doc_ids.append(doc_id)
                logger.info(
                    "a2a file accepted (uri): name=%s uri=%s mime=%s doc_id=%s",
                    display_name,
                    file.uri,
                    file.mime_type,
                    doc_id,
                )
                continue

            # Unknown File shape — log and pass through (ADK's converter
            # may handle it; if not, it'll be dropped downstream)
            logger.warning("a2a file with unknown shape: %r — passing through", type(file).__name__)
            new_parts.append(part)

        # No FileParts found → no-op return (text-only invocations untouched).
        if not new_doc_ids and not rejected:
            return context

        # Mutate message.parts in place (Message model_config.frozen=False,
        # verified during design).
        context.message.parts = new_parts

        # Write to session state via the runner's session service.
        if new_doc_ids and session_id:
            await _inject_document_ids(
                runner=runner,
                app_name=app_name,
                user_id=request_user_id,
                session_id=session_id,
                new_doc_ids=new_doc_ids,
            )

        if rejected:
            # Tack a synthetic TextPart explaining what was rejected so the
            # peer sees feedback in the Task's text response. Format chosen
            # to be model-readable: "I see your file <name> was rejected
            # because <reason>" is more useful than silent drop.
            from a2a.types import Part as A2APart
            from a2a.types import TextPart

            joined = "; ".join(f"{name}: {reason}" for name, reason in rejected)
            note = (
                f"Note: {len(rejected)} attached file(s) could not be processed: {joined}. "
                "Supported formats: PDF, DOCX, XLSX, PPTX, ODT, EML, CSV, plain text (≤ 25 MB)."
            )
            context.message.parts.append(A2APart(root=TextPart(text=note)))

        return context

    return ExecuteInterceptor(before_agent=_before_agent)


async def _inject_document_ids(
    *,
    runner: Runner,
    app_name: str,
    user_id: str,
    session_id: str,
    new_doc_ids: list[str],
) -> None:
    """Append doc_ids to `state["document_ids"]` for the A2A session.

    Pattern handles both the new-session and continuing-session cases.
    ADK's executor will create the session if missing, but our interceptor
    runs BEFORE that. So:
      - If session exists, append via event with state_delta
      - If not, create with initial state
    """
    try:
        session = await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    except Exception:
        logger.exception("a2a interceptor: get_session failed; injecting via create")
        session = None

    if session is None:
        try:
            await runner.session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state={
                    _STATE_DOCUMENT_IDS: list(new_doc_ids),
                    # Mark as already-loaded so doc_loader's Firestore
                    # lookup is skipped (our artifact is pre-materialised).
                    _STATE_DOCS_LOADED: list(new_doc_ids),
                },
            )
            logger.info(
                "a2a interceptor: created session %s with %d document_ids",
                session_id,
                len(new_doc_ids),
            )
        except Exception:
            logger.exception("a2a interceptor: create_session failed; state injection skipped")
        return

    # Existing session — append an event carrying the state delta.
    existing = list(session.state.get(_STATE_DOCUMENT_IDS) or [])
    merged = existing + [d for d in new_doc_ids if d not in existing]
    # Same merge logic for docs_loaded so doc_loader skips our pre-saved artifacts.
    loaded_existing = list(session.state.get(_STATE_DOCS_LOADED) or [])
    loaded_merged = loaded_existing + [d for d in new_doc_ids if d not in loaded_existing]

    try:
        from google.adk.events.event import Event

        event = Event(
            invocation_id=str(uuid.uuid4()),
            author="a2a_file_extraction",
            state_delta={
                _STATE_DOCUMENT_IDS: merged,
                _STATE_DOCS_LOADED: loaded_merged,
            },
        )
        await runner.session_service.append_event(session, event)
        logger.info(
            "a2a interceptor: appended state_delta to session %s (document_ids -> %d items)",
            session_id,
            len(merged),
        )
    except Exception:
        logger.exception("a2a interceptor: append_event failed; state injection partial")
