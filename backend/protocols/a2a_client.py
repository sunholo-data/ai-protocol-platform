"""Outbound A2A client — invoke a resident agent as a peer.

The platform has served A2A since v6.17.0 (`protocols/a2a.py`,
`a2a_invocation.py` at `/a2a`) but has never *called* a peer. v6.40.0 needs
that direction: a resident agent instance is an A2A peer, and the alternative
was a bespoke REST surface, which is what design Decision 2c rejected.

Deliberately thin. Everything protocol-shaped comes from `a2a-sdk` (already a
dependency) rather than hand-rolled JSON-RPC, and everything auth-shaped comes
from `tools.mcp.id_token_cache`, the same path `tools/browser.py` uses to reach
an IAM-gated Cloud Run service. What is left here is the glue and the
fail-closed rules.

Not specific to resident agents: any A2A peer works. That is the point of
adopting the protocol rather than inventing one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TextPart,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from tools.mcp.id_token_cache import IdTokenUnavailable, get_id_token

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("A2A_CLIENT_TIMEOUT_SECONDS", "60"))

# `A2AClient` is deprecated in a2a-sdk in favour of `ClientFactory`, so this
# module uses the factory: new code on a path the SDK has already announced it
# will remove is debt taken on knowingly. The factory also requires the agent
# card before invocation, which is the discovery-then-invoke order A2A intends,
# and returns typed objects instead of JSON-RPC envelopes to unwrap by hand.
_CARD_CACHE: dict[str, AgentCard] = {}


def clear_card_cache() -> None:
    """Drop resolved agent cards (tests, and after a peer is redeployed)."""
    _CARD_CACHE.clear()


class A2APeerError(RuntimeError):
    """A peer could not be reached, or answered with an error.

    Raised rather than returning a sentinel: a caller that silently treats an
    unreachable agent as "no work to do" is the failure mode NEVER SILENT
    exists to prevent.
    """


class A2ATaskTimeout(A2APeerError):
    """The peer took the work and had not finished when we stopped waiting.

    A subclass rather than a flag on A2APeerError, because "still working" and
    "could not be reached" are DIFFERENT FACTS and were being reported as the
    same one: a healthy long-running task came back to the user as a broken
    agent. A subclass so every existing `except A2APeerError` keeps catching
    it — narrowing that would turn a handled timeout into an unhandled
    exception in the middle of a chat turn.

    Carries the task id, which is what a caller needs to say "the answer will
    arrive later" and mean it.
    """

    def __init__(self, message: str, *, task_id: str, last_state: str) -> None:
        super().__init__(message)
        self.task_id = task_id
        self.last_state = last_state


def _auth_headers(base_url: str) -> dict[str, str]:
    """Bearer ID token for IAM-gated peers; nothing for local/plain HTTP.

    Mirrors `tools/browser.py`: only https peers get a token, so LOCAL_MODE and
    tests against a stub transport do not need ambient credentials.
    """
    if not base_url.startswith("https://"):
        return {}
    try:
        return {"Authorization": f"Bearer {get_id_token(base_url)}"}
    except IdTokenUnavailable as exc:
        raise A2APeerError(f"no ID token for peer {base_url}: {exc}") from exc


# W3C trace context, so a resident run lands INSIDE the chain that caused it
# (v6.40.0 M8). The resident continues an inbound `traceparent`
# (docker/resident/server.mjs) — but until this, nobody sent one: httpx is not
# instrumented here, so every resident run opened a trace of its own sitting
# beside the platform's, and the correlation the design asked for was half-built
# on each side and joined nowhere.
#
# Module-level so a test can swap it, and read through a name rather than called
# directly so the swap actually takes effect.
_propagator = TraceContextTextMapPropagator()


def _peer_headers(base_url: str) -> dict[str, str]:
    """Auth plus trace context.

    Propagation is wrapped because TELEMETRY MUST NEVER BE THE REASON A PEER
    CANNOT BE REACHED. A broken propagator costs the trace; dropping the
    Authorization header with it would cost the call, turning a telemetry fault
    into a 401 from the peer.
    """
    headers = _auth_headers(base_url)
    try:
        _propagator.inject(headers)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("a2a: trace context not propagated to %s: %s", base_url, exc)
    return headers


def _client(base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers=_peer_headers(base_url),
        transport=transport,
    )


async def fetch_agent_card(base_url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    """Resolve a peer's agent card. The SDK's default path is
    `/.well-known/agent-card.json`; resident instances serve that and the
    older `/.well-known/agent.json` spelling, so discovery works either way.
    """
    base_url = base_url.rstrip("/")
    async with _client(base_url, transport=transport) as http:
        try:
            return await A2ACardResolver(httpx_client=http, base_url=base_url).get_agent_card()
        except Exception as exc:
            raise A2APeerError(f"agent card unreachable at {base_url}: {exc}") from exc


async def send_message(
    base_url: str,
    text: str,
    *,
    task_id: str | None = None,
    context_id: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    push_url: str | None = None,
    push_token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Send a message to a peer, or continue an existing task.

    Passing `task_id` continues that task — which is how A2A answers an agent
    in `input-required`, so no reply mechanism of our own is needed.

    `context_id` is how you hold a CONVERSATION. A2A defines it as the
    identifier for an exchange spanning several tasks, and the resident agent
    keys pi's session on it, so reusing one resumes the thread and a new one
    starts a fresh assistant. Omitting it gets a one-shot with no memory —
    correct for a job, wrong for a chat.

    `run_id` is offered to the peer as the task id so the platform's run record
    and the peer's task share one identifier instead of a correlation table.
    `push_url` registers a webhook up front, because a resident agent may block
    hours after this call returns with nobody connected.
    """
    base_url = base_url.rstrip("/")
    metadata: dict[str, Any] = {}
    if model:
        metadata["model"] = model
    if run_id:
        metadata["runId"] = run_id

    message = Message(
        role=Role.user,
        message_id=str(uuid.uuid4()),
        parts=[Part(root=TextPart(text=text))],
        task_id=task_id,
        context_id=context_id,
        metadata=metadata or None,
    )

    push_configs = []
    if push_url:
        push_configs.append(PushNotificationConfig(url=push_url, token=push_token))

    async with _client(base_url, transport=transport) as http:
        card = await _card(base_url, http, transport=transport)
        client = _factory(http, push_configs).create(card)
        try:
            # streaming=False, so the iterator yields exactly one item; the
            # resident card declares `streaming: false` to match.
            async for event in client.send_message(message, request_metadata=metadata or None):
                return _as_dict(event)
        except Exception as exc:
            raise A2APeerError(f"message/send to {base_url} failed: {exc}") from exc
    raise A2APeerError(f"message/send to {base_url} yielded no response")


TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected", "input-required"})


def _state_of(task: dict[str, Any]) -> str:
    return str((task.get("status") or {}).get("state") or "")


def answer_of(task: dict[str, Any]) -> str:
    """The assistant's text from a finished task.

    Reads the artifact rather than the status message: the artifact is kept
    current as deltas arrive, so it is also what a caller sees mid-run.
    """
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("kind") == "text" and part.get("text"):
                return str(part["text"])
    return ""


async def converse(
    base_url: str,
    text: str,
    *,
    context_id: str,
    model: str | None = None,
    run_id: str | None = None,
    push_url: str | None = None,
    push_token: str | None = None,
    timeout_s: float = 180.0,
    poll_s: float = 2.0,
    transport: httpx.AsyncBaseTransport | None = None,
    on_poll: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """One turn of a conversation: send, then poll until the peer is done.

    The resident agent answers `message/send` immediately with a `submitted`
    task and does the work in the background, so a caller that returns here
    without polling has learned only that the request was accepted. That
    distinction is the whole reason this helper exists rather than each caller
    inventing its own loop and its own idea of "finished".

    `push_url` is forwarded to the send, so a task that outlives this wait can
    still reach the caller later. Registering it HERE rather than afterwards
    matters: the peer accepts the config on `message/send` itself
    (ailang docker/resident/lib/a2a.mjs:321), so there is no window in which
    the task exists with nobody watching it.

    ``on_poll`` sees every task this loop fetches, the first one included. The
    peer keeps the task's artifacts current mid-run, so each poll already
    carries real progress and this loop THREW ALL OF IT AWAY, returning only
    the terminal task. That discarded progress is what M7's session story
    reads; nothing else here changes.

    It is an observer. Exceptions from it are swallowed, because a decoration
    must never be able to fail the turn it decorates.

    Raises A2ATaskTimeout on timeout, naming the task and the last state seen —
    never returns a half-finished task dressed as an answer.
    """

    def _observe(t: dict[str, Any]) -> None:
        if on_poll is None:
            return
        try:
            on_poll(t)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("converse: on_poll observer raised (suppressed): %s", exc)

    task = await send_message(
        base_url,
        text,
        context_id=context_id,
        model=model,
        run_id=run_id,
        push_url=push_url,
        push_token=push_token,
        transport=transport,
    )
    task_id = task.get("id")
    if not task_id:
        raise A2APeerError(f"peer at {base_url} returned no task id: {str(task)[:200]}")

    _observe(task)
    deadline = time.monotonic() + timeout_s
    state = _state_of(task)
    while state not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            raise A2ATaskTimeout(
                f"task {task_id} at {base_url} did not finish within {timeout_s:g}s (last state: {state or 'unknown'})",
                task_id=task_id,
                last_state=state or "unknown",
            )
        await asyncio.sleep(poll_s)
        task = await get_task(base_url, task_id, transport=transport)
        _observe(task)
        state = _state_of(task)
    return task


async def get_task(base_url: str, task_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    async with _client(base_url, transport=transport) as http:
        card = await _card(base_url, http, transport=transport)
        client = _factory(http, []).create(card)
        try:
            task = await client.get_task(TaskQueryParams(id=task_id))
        except Exception as exc:
            raise A2APeerError(f"tasks/get {task_id} at {base_url} failed: {exc}") from exc
    return _as_dict(task)


async def set_push_config(
    base_url: str,
    task_id: str,
    push_url: str,
    *,
    push_token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Register a webhook for an existing task, for peers that did not take one
    on `message/send`."""
    base_url = base_url.rstrip("/")
    async with _client(base_url, transport=transport) as http:
        card = await _card(base_url, http, transport=transport)
        client = _factory(http, []).create(card)
        try:
            cfg = await client.set_task_callback(
                TaskPushNotificationConfig(
                    task_id=task_id,
                    push_notification_config=PushNotificationConfig(url=push_url, token=push_token),
                )
            )
        except Exception as exc:
            raise A2APeerError(f"pushNotificationConfig/set for {task_id} failed: {exc}") from exc
    return _as_dict(cfg)


async def _card(base_url: str, http: httpx.AsyncClient, *, transport=None) -> AgentCard:
    """Resolve and cache the peer's card. Cached because the factory needs it on
    every call and a resident peer's card changes only on redeploy."""
    if base_url in _CARD_CACHE:
        return _CARD_CACHE[base_url]
    try:
        card = await A2ACardResolver(httpx_client=http, base_url=base_url).get_agent_card()
    except Exception as exc:
        raise A2APeerError(f"agent card unreachable at {base_url}: {exc}") from exc
    _CARD_CACHE[base_url] = card
    return card


def _factory(http: httpx.AsyncClient, push_configs: list[PushNotificationConfig]) -> ClientFactory:
    return ClientFactory(ClientConfig(httpx_client=http, streaming=False, push_notification_configs=push_configs))


def _as_dict(event: Any) -> dict[str, Any]:
    """Normalise what the SDK yields into a plain dict.

    `send_message` yields either a `Message` or a `(Task, update)` tuple; the
    task is what callers care about, and a Message-only reply means the peer
    answered without creating a task.
    """
    if isinstance(event, tuple):
        event = event[0]
    if hasattr(event, "model_dump"):
        return event.model_dump(by_alias=True, exclude_none=True, mode="json")
    return dict(event)
