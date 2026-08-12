"""Confidentiality invariant for the AG-UI SSE boundary (v6.19.0, AIPLA #39).

**The invariant: a tool result is privileged until something says otherwise.**

Why this exists. `TOOL_CALL_RESULT` events are mirrored onto the client stream,
and the client *renders* them — `useSkillAgent` stores `event.content` as the
Activity chip's `resultContent`. So a server-side tool whose result is
confidential is both displayed in the Activity panel and readable in devtools.
Nothing in the template marked a result as not-client-visible.

The reporting fork hit this with a judging tool that returned the teacher's
expected answers and rubric to student sessions. It is a *generic* hole: any
deployment with a privileged server tool and a lower-trust audience leaks by
default, which is squarely against this repo's rule that a confidential
derivative must sit behind the same gate as its source.

Why we never saw it: every session we test with is a trusted owner session. It
took a fork with two real trust levels to surface it.

Design notes
------------

* **Deny by default.** A new tool is private unless it is registered as
  client-renderable. The alternative (allow by default, mark the secret ones)
  fails open on every tool anyone forgets to mark — which is how we got here.

* **Fails closed on an unmatched id.** AG-UI result events carry only
  `toolCallId`, never the tool name, so the name must be learned from the
  earlier `TOOL_CALL_START`. If that pairing is missing (reordering, a dropped
  prelude, a future adapter), we redact rather than guess.

* **Redact, don't drop.** The event still goes out with neutral content, so the
  Activity chip still shows *that* the tool ran (NEVER SILENT — the user must
  not silently lose feedback) while the payload is withheld.

* **The allowlist is registry-driven**, not a second hardcoded list. A tool
  whose result feeds a registered result→A2UI mapping is by definition meant to
  reach the client. Reusing `a2ui_result_render.is_render_payload_tool` keeps
  this from drifting the way a parallel list would (the retired
  `_RENDER_PAYLOAD_TOOLS` set is the cautionary precedent).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from adk.a2ui_result_render import is_render_payload_tool

logger = logging.getLogger(__name__)

# Placeholder substituted for a withheld payload. Deliberately not empty string:
# a consumer that renders it should show something honest rather than a blank.
REDACTED_CONTENT = "[redacted: tool result not available in this session]"

# Tools whose results the client MUST parse to function, independent of the
# result→A2UI registry.
#
# `send_a2ui_json_to_client` is the in-model A2UI path: `useActionDrivenAgent`
# parses its TOOL_CALL_RESULT content to build the surface. Redacting it breaks
# A2UI rendering outright.
ALWAYS_CLIENT_VISIBLE: frozenset[str] = frozenset(
    {
        "send_a2ui_json_to_client",
    }
)

_TOOL_CALL_START = "TOOL_CALL_START"
_TOOL_CALL_RESULT = "TOOL_CALL_RESULT"


def is_client_visible_tool(tool_name: str) -> bool:
    """True when this tool's raw result is meant to reach the client.

    Registry-driven plus a small explicit set for tools the client parses
    directly. Anything else is privileged.
    """
    if tool_name in ALWAYS_CLIENT_VISIBLE:
        return True
    return is_render_payload_tool(tool_name)


def session_is_lower_trust(auth_mode: str, group_id: str) -> bool:
    """True when the caller is a shared/anonymous audience rather than an owner.

    Anonymous-group sessions authenticate with a *shared* code: everyone holding
    the link is the same principal, so a result meant for an operator must not
    be assumed safe. Firebase / Identity-Platform / LOCAL_MODE callers are
    individually identified owners and keep full visibility.

    Takes primitives rather than the `User` model so this module stays
    importable from tests without constructing auth objects.
    """
    return auth_mode == "anonymous_group_id" or bool(group_id)


async def redact_privileged_results(
    events: AsyncIterator[dict],
    *,
    lower_trust: bool,
    thread_id: str = "<unknown>",
) -> AsyncIterator[dict]:
    """Withhold non-client-visible tool-result payloads from lower-trust sessions.

    Pass-through (zero copying, zero state) when ``lower_trust`` is False, so the
    owner path is unchanged.

    Args:
        events: the upstream AG-UI event dicts (camelCase aliases, as produced by
            ``model_dump(by_alias=True)``).
        lower_trust: see :func:`session_is_lower_trust`.
        thread_id: for log correlation only.
    """
    if not lower_trust:
        async for event in events:
            yield event
        return

    # toolCallId -> toolCallName, learned from TOOL_CALL_START.
    names: dict[str, str] = {}
    redacted_count = 0

    async for event in events:
        event_type = event.get("type")

        if event_type == _TOOL_CALL_START:
            call_id = event.get("toolCallId")
            tool_name = event.get("toolCallName")
            if isinstance(call_id, str) and isinstance(tool_name, str):
                names[call_id] = tool_name
            yield event
            continue

        if event_type != _TOOL_CALL_RESULT:
            yield event
            continue

        call_id = event.get("toolCallId")
        tool_name = names.get(call_id) if isinstance(call_id, str) else None

        if tool_name is not None and is_client_visible_tool(tool_name):
            yield event
            continue

        # Privileged, or unpairable (fail closed).
        redacted_count += 1
        reason = "not_client_visible" if tool_name is not None else "unmatched_tool_call_id"
        logger.info(
            "stream_redaction: withheld tool result (tool=%s, reason=%s, tool_call_id=%s, thread_id=%s)",
            tool_name or "<unknown>",
            reason,
            call_id,
            thread_id,
        )
        yield {**event, "content": REDACTED_CONTENT}

    if redacted_count:
        logger.info(
            "stream_redaction: withheld %d tool result(s) for a lower-trust session (thread_id=%s)",
            redacted_count,
            thread_id,
        )
