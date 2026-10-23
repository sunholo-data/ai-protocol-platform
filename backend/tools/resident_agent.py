"""`ask_assistant` — put a question to the user's resident agent.

The resident is a long-lived coding agent on a Cloud Run instance (AILANG
estate, `docs/design/v6.40.0/resident-agent-instances.md`). This platform
reaches it as an A2A PEER, not through an API of ours: it runs in a different
project under a different identity, and A2A is precisely the seam that makes
that a feature rather than a hole.

WHY A SEPARATE TOOL FROM `browse`, `rag_tool` AND FRIENDS
Those answer a question inside this turn. The resident holds a CONVERSATION
that outlives the request, with its own workspace, its own files and its own
memory of what was said last time. Asking it something is delegation, not a
lookup.

THREAD IDENTITY IS DERIVED, NEVER PASSED IN
A2A's `contextId` keys the resident's session, so whoever names the contextId
chooses which conversation to read. It is therefore built from the ADK
invocation's user and session and is NOT a parameter of this tool — a model
that could pass one could ask for somebody else's thread by guessing it. The
model chooses only whether to continue this session's thread or start a fresh
one.

ISOLATION DEPENDS ON A FLAG, AND THE FLAG IS THE WHOLE STORY
With `RESIDENT_AGENT_PER_USER` on — dev since 2026-09-04 — each caller reaches
their OWN instance, resolved from their own record, with its own gcsfuse
`only-dir` mount. With it off, one shared instance serves everybody and
`contextId` separates CONVERSATIONS, not DATA: one agent home, one workspace,
one identity behind them all. In that mode this tool must not be offered to a
skill handling one user's confidential material on behalf of another.

AN ANSWER CAN ARRIVE AFTER THE TURN ENDS, AND ONLY ONE THING OWNS IT
A question can outlive the wait below. While the wait is running the POLL owns
the task and the answer comes back inside this turn. Only when the poll gives
up is a per-task webhook registered, handing ownership to the receiver
(`internal_tasks/resident_routes.py`), which appends the answer to this
conversation when it lands. Registering the webhook up front instead — the
first shape of this — had both watching at once, so a question that finished
in 20 seconds was answered twice: once by the tool, once by the receiver
prefixed "from earlier".

With no receiver configured (`RESIDENT_PUSH_BASE_URL` unset) a slow question is
reported as slow and the user is asked to try again, because promising a
delivery nothing can make is worse than admitting there is none.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

RESIDENT_AGENT_URL = os.environ.get("RESIDENT_AGENT_URL", "").rstrip("/")
# Per-user mode resolves each caller's OWN instance from their record. Off, the
# single shared instance in RESIDENT_AGENT_URL is used — which is correct for a
# one-developer deployment and WRONG the moment two users share it, because
# contextId separates conversations and not data (design D11/P3).
#
# An explicit mode rather than "use a record if one happens to exist": a silent
# fallback from per-user to shared is precisely how one user's question would
# end up answered on another user's box.
RESIDENT_PER_USER = os.environ.get("RESIDENT_AGENT_PER_USER", "").lower() in ("1", "true", "yes", "on")
# Lifecycle endpoint that can START a stopped instance. The platform decides
# that a user needs their agent; the AILANG estate performs the act (design P2),
# because a customer-facing backend should not hold the power to operate the
# estate that runs the agent fleet.
#
# A stopped instance does NOT wake on a request — its URL 404s until something
# calls :start, and it takes ~30s once asked (measured 2026-09-03). Without this
# configured, an idle-stopped agent is simply unreachable, and the user is told
# that plainly rather than shown a transport error.
RESIDENT_START_ENDPOINT = os.environ.get("RESIDENT_START_ENDPOINT", "").rstrip("/")
RESIDENT_WAKE_TIMEOUT_S = float(os.environ.get("RESIDENT_WAKE_TIMEOUT_SECONDS", "90"))
# Where the resident should POST a task update when it finishes. The platform's
# OWN public origin — which is the frontend service, not the standalone
# backend: that one is deployed --no-allow-unauthenticated and the instance
# holds no credential for it, while the frontend is public and its
# `/api/proxy/**` catch-all forwards the Authorization header untouched to this
# sidecar.
#
# UNSET IS NOT AN ERROR. No webhook is registered, no record is written, and
# the tool behaves exactly as it did before M5a — which is what lets the code
# reach an environment before its configuration does.
RESIDENT_PUSH_BASE_URL = os.environ.get("RESIDENT_PUSH_BASE_URL", "").rstrip("/")
RESIDENT_AGENT_MODEL = os.environ.get("RESIDENT_AGENT_MODEL", "") or None
RESIDENT_AGENT_TIMEOUT_S = float(os.environ.get("RESIDENT_AGENT_TIMEOUT_SECONDS", "180"))
# How many past turns the workbench story keeps. The RESIDENT remembers the
# whole conversation regardless — this is only how much of it is drawn, and
# the Basic catalog has no scroll container, so it is a height budget.
STORY_MAX_TURNS = int(os.environ.get("RESIDENT_STORY_MAX_TURNS", "12"))


def _identity(tool_context: Any) -> tuple[str, str]:
    """Caller identity from the ADK invocation, same shape as `tools/browser.py`."""
    user_id = "unknown"
    session_id = "unknown"
    try:
        invocation = getattr(tool_context, "_invocation_context", None)
        if invocation is not None:
            user_id = getattr(invocation, "user_id", None) or user_id
            session = getattr(invocation, "session", None)
            session_id = getattr(session, "id", None) or session_id
    except Exception:  # pragma: no cover - defensive
        pass
    return user_id, session_id


def thread_id(user_id: str, session_id: str, *, fresh: bool = False) -> str:
    """The A2A contextId for this caller's thread.

    Hashed rather than concatenated so a user id — which may be an email — does
    not travel to another project's logs in clear text, and so the id is a
    predictable shape whatever the inputs look like. Stable for a session, so a
    follow-up question lands in the same conversation without anything being
    stored on this side.

    `fresh` mixes in the session's own entropy... deliberately NOT a random
    value, because a thread nobody can name again is a thread nobody can
    resume. Starting over means a new ADK session.
    """
    seed = f"{user_id}|{session_id}|{'fresh' if fresh else 'main'}"
    return "aitana-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _resolve_agent(user_id: str) -> tuple[str, dict | None]:
    """Which instance answers for this user — or why none does.

    Returns ``(base_url, None)`` or ``("", error_payload)``. Every failure is a
    DISTINCT message, because "you may not have one", "yours is still starting"
    and "yours broke" are three different facts and a single shrug for all
    three is the failure mode this design keeps meeting (design P5).
    """
    if not RESIDENT_PER_USER:
        if not RESIDENT_AGENT_URL:
            # Fail closed and SAY SO: a tool that quietly returns "I don't know"
            # when it is simply unconfigured teaches the model to stop asking.
            return "", {
                "error": "No resident assistant is configured for this deployment (RESIDENT_AGENT_URL is unset).",
                "configured": False,
            }
        return RESIDENT_AGENT_URL, None

    from db import resident_agents as records

    try:
        rec = records.get(user_id)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("resident_agent: record lookup failed for %s: %s", user_id, exc)
        return "", {"error": f"Could not look up your assistant: {exc}"}

    if rec is None:
        return "", {
            "error": (
                "You do not have a resident assistant. It is granted per user — ask a "
                "platform or tenant administrator for the 'resident-user' role."
            ),
            "eligible": False,
        }
    if rec.state == records.STATE_READY and rec.url:
        records.touch(user_id)
        return rec.url.rstrip("/"), None
    if rec.state in (records.STATE_REQUESTED, records.STATE_PROVISIONING):
        return "", {"error": "Your assistant is still being set up. Try again in a minute.", "state": rec.state}
    if rec.state == records.STATE_REVOKED:
        return "", {
            "error": "Your resident assistant has been revoked. An administrator can restore it.",
            "state": rec.state,
        }
    return "", {
        "error": f"Your assistant is not available (state: {rec.state}). {rec.error or ''}".strip(),
        "state": rec.state,
    }


def _instance_label(user_id: str) -> str:
    """The instance's NAME, for the story header — or "" rather than a guess.

    CLAUDE.md #9: what a human reads is a name. In per-user mode the record
    holds it (`resident-0fca45abec2c`, the same string `resident-instance.sh
    --name` takes). In shared mode there is no record and the only thing
    available is a Cloud Run hostname with a project hash glued on, which is
    an address rather than a name — so nothing is shown. An absent fact beats
    an ugly one dressed as a fact.

    One read per TURN, not per poll tick, and fail-open: a story header is
    never worth failing a turn for.
    """
    if not RESIDENT_PER_USER:
        return ""
    try:
        from db import resident_agents as records

        rec = records.get(user_id)
        return str(getattr(rec, "instance", "") or "")
    except Exception:  # pragma: no cover - defensive
        return ""


def _story_observer(tool_context: Any, user_id: str, question: str = "") -> Any:
    """An `on_poll` for `converse` that keeps the session-story surface current.

    WHY THE TOOL EMITS AT ALL, given the golden rule that a tool result becomes
    UI in exactly ONE place — a registered mapping. The mapping still owns the
    FINAL surface; it cannot own the intermediate ones, because
    `after_tool_callback` fires once, at the end, and the whole point here is
    the 180 seconds BEFORE that. Both paths call `build_story`, and a test
    asserts they produce the same tree, so the exception cannot drift into two
    different stories.

    Returns None when there is nothing to do, so `converse` keeps its fast path.
    """
    from adk.a2ui_resident_render import (
        RESIDENT_SURFACE,
        STORY_STATE_KEY,
        _artifact_meta,
        build_story,
        story_enabled,
    )

    if not story_enabled():
        return None
    state = getattr(tool_context, "state", None) if tool_context is not None else None
    if state is None:
        return None

    from observability.timing import get_current_tracker, has_current_tracker

    if not has_current_tracker():
        # Not fatal — the mapping still renders the final surface when the turn
        # ends. But the progressive half is silently doing nothing, and silence
        # is exactly how the compare-launcher bug survived (backend/adk/CLAUDE.md).
        log.warning(
            "resident story: no LatencyTracker bound to this context, so live updates "
            "cannot reach the wire; only the final surface will render"
        )

    instance = _instance_label(user_id)
    started = time.monotonic()
    ticks = {"n": 0}

    def on_poll(task: dict) -> None:
        # ONE ENTRY PER TURN, ACCUMULATED. This used to replace the whole story
        # each turn, so the workbench showed the current question and silently
        # forgot the three before it — while the RESIDENT remembered all of
        # them, because contextId is stable for the session. The surface was
        # the only party losing the thread.
        #
        # The turn is keyed by task id and rewritten in place on every tick, so
        # re-entry is idempotent however often the loop polls.
        turn = {
            "task_id": task.get("id") or "",
            "question": question,
            "task": task,
            "state": str((task.get("status") or {}).get("state") or "unknown"),
            "elapsed_s": time.monotonic() - started,
        }
        try:
            prior = state.get(STORY_STATE_KEY)
            turns = list(prior.get("turns", [])) if isinstance(prior, dict) else []
            at = next((i for i, t in enumerate(turns) if t.get("task_id") == turn["task_id"]), -1)
            if at >= 0:
                turns[at] = turn
            else:
                turns.append(turn)
            state[STORY_STATE_KEY] = {
                "turns": turns[-STORY_MAX_TURNS:],
                "instance": instance,
                "model": RESIDENT_AGENT_MODEL or "",
            }
            story = state[STORY_STATE_KEY]
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("resident story: could not record story (%s)", exc)
            return

        messages = build_story(story)
        if not messages:
            return
        ticks["n"] += 1
        # SAME surfaceId every tick, so the frontend UPDATES one surface rather
        # than accumulating a tab per poll — the failure that looks like
        # success. The sourceId must differ per emission, because it is the
        # registry's idempotency key.
        invocation = getattr(tool_context, "invocation_id", "") or ""
        get_current_tracker().emit_a2ui_surface(
            RESIDENT_SURFACE,
            messages,
            f"{invocation}:ask_assistant:poll:{ticks['n']}",
            _artifact_meta(None),
        )

    return on_poll


async def _is_asleep(base_url: str) -> bool:
    """True when the peer looks stopped rather than broken.

    /livez is the one unauthenticated route, so this needs no credentials. A
    stopped instance 404s from the Cloud Run frontend; a running one answers
    "ok". Anything else is neither, and is reported as itself.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{base_url}/livez")
        return r.status_code == 404
    except Exception:
        return False


# The coordinator mounts its lifecycle handler at `/instances/start`
# (ailang internal/coordinator/daemon_http.go). RESIDENT_START_ENDPOINT stays
# the bare SERVICE base URL rather than carrying this path, because the same
# value is the ID-token audience — and the coordinator checks that audience
# against its own base URL (RESIDENT_LIFECYCLE_AUDIENCE). Fold the path in here
# and a token minted for the service still verifies; bake it into the env var
# and the audience gains a path the coordinator will not accept.
#
# Verified live 2026-09-04, after posting to `/start` returned a bare
# "404 page not found" — the 23 unit tests around this all stubbed `_wake`
# itself, so the one line that had to match another repo was the one line
# nothing checked.
_LIFECYCLE_START_PATH = "/instances/start"


async def _wake(base_url: str) -> bool:
    """Ask the lifecycle endpoint to start this instance. False if it cannot.

    Fire-and-return: starting takes ~30s and the caller is a chat turn, so this
    reports "starting" rather than blocking a user behind it.
    """
    if not RESIDENT_START_ENDPOINT:
        return False
    import httpx

    from tools.mcp.id_token_cache import get_id_token

    try:
        headers = {}
        if RESIDENT_START_ENDPOINT.startswith("https://"):
            headers["Authorization"] = f"Bearer {get_id_token(RESIDENT_START_ENDPOINT)}"
        async with httpx.AsyncClient(timeout=RESIDENT_WAKE_TIMEOUT_S) as http:
            r = await http.post(
                f"{RESIDENT_START_ENDPOINT}{_LIFECYCLE_START_PATH}",
                json={"url": base_url},
                headers=headers,
            )
        if r.status_code >= 400:
            log.warning("resident_agent: wake refused %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:  # IdTokenUnavailable included — both are "could not wake"
        log.warning("resident_agent: wake failed: %s", exc)
        return False


async def _arrange_late_delivery(base_url: str, task_id: str, user_id: str, session_id: str) -> dict:
    """Hand the task over to the webhook, now that nobody is polling it.

    ONE OWNER AT A TIME, and this is the handover. While `converse` polls, the
    POLL is watching the task and the answer comes back inside the turn. A
    webhook registered up front — which is how this was written first — meant
    the peer pushed the answer at the same moment the poll was about to return
    it, so the user was told twice, the second time prefixed "from earlier".

    Returns `{"arranged": True}`, or `{"task": <task>}` when it finished in the
    gap, or `{}` when late delivery is not possible and must not be promised.

    Never raises. Losing the ability to deliver an answer LATER must not cost
    the answer in hand.
    """
    if not RESIDENT_PUSH_BASE_URL:
        return {}
    if user_id == "unknown" or session_id == "unknown":
        # A record keyed to nobody would mint a live token for an answer that
        # can never be delivered — `get_session` has nothing to look up.
        log.warning("resident_agent: no caller identity; not arranging late delivery for %s", task_id)
        return {}

    from protocols.a2a_client import get_task, set_push_config

    try:
        from adk.agui import APP_NAME
        from db import resident_tasks
        from internal_tasks.resident_routes import NOTIFY_PATH

        _rec, token = resident_tasks.create(
            task_id,
            user_id=user_id,
            session_id=session_id,
            app_name=APP_NAME,
        )
        await set_push_config(
            base_url,
            task_id,
            push_url=f"{RESIDENT_PUSH_BASE_URL}{NOTIFY_PATH}",
            push_token=token,
        )
    except Exception as exc:
        log.warning("resident_agent: late delivery not arranged for %s (%s)", task_id, exc)
        return {}

    # Close the gap. The task could have reached a terminal state between the
    # last poll and the registration above, and the peer only notifies on a
    # TRANSITION — so an answer that landed in those milliseconds would sit on
    # the instance for ever with a webhook that will never fire for it.
    try:
        task = await get_task(base_url, task_id)
    except Exception:
        return {"arranged": True}
    state = str((task.get("status") or {}).get("state") or "")
    if state in ("completed", "failed", "canceled", "rejected"):
        try:
            from db import resident_tasks as records

            records.mark_delivered(task_id, answer="(returned in the originating turn)")
        except Exception:  # pragma: no cover - the claim is an optimisation here
            pass
        return {"task": task}
    return {"arranged": True}


async def ask_assistant(message: str, tool_context: Any = None) -> dict:
    """Ask the user's resident assistant and wait for its answer.

    Args:
        message: what to ask. The assistant remembers this conversation, so
            follow-ups can refer back to earlier turns without repeating them.

    Returns:
        A dict with `answer`, plus `thread` and `task_id` for tracing. On
        failure it returns `error` with a human-readable reason — never a
        partial answer dressed as a complete one.
    """
    user_id, session_id = _identity(tool_context)
    base_url, err = _resolve_agent(user_id)
    if err:
        return err

    # Imported here so a deployment without the a2a SDK on the path still loads
    # the tool module and returns the clear errors above.
    from protocols.a2a_client import A2APeerError, A2ATaskTimeout, answer_of, converse

    context_id = thread_id(user_id, session_id)

    try:
        task = await converse(
            base_url,
            message,
            context_id=context_id,
            model=RESIDENT_AGENT_MODEL,
            timeout_s=RESIDENT_AGENT_TIMEOUT_S,
            on_poll=_story_observer(tool_context, user_id, question=message),
        )
    except A2ATaskTimeout as exc:
        # THE PEER ANSWERED AND TOOK THE WORK. Reporting this as "could not be
        # reached" — which is what happened before A2ATaskTimeout existed —
        # told the user their assistant was broken while it was busy, and
        # invited them to start an agent that was already running.
        late = await _arrange_late_delivery(base_url, exc.task_id, user_id, session_id)
        if late.get("task") is not None:
            task = late["task"]  # finished in the gap; answer it here and now
        elif late.get("arranged"):
            return {
                "state": "working",
                "task_id": exc.task_id,
                "thread": context_id,
                "note": (
                    "Your assistant is still working on this. The answer will appear in this "
                    "conversation when it lands — tell the user they do not need to ask again."
                ),
            }
        else:
            # Nothing will bring the answer back. Saying otherwise is a promise
            # the user only discovers is empty by waiting.
            return {
                "error": (
                    f"Your assistant is still working (state: {exc.last_state}) and this deployment "
                    "cannot deliver the answer once this turn ends. Ask again shortly."
                ),
                "state": exc.last_state,
                "thread": context_id,
            }
    except A2APeerError as exc:
        # ASLEEP AND BROKEN ARE DIFFERENT FACTS. A stopped instance answers 404
        # from the Cloud Run frontend, which as a transport error is
        # indistinguishable from a crash — and telling a user their assistant is
        # broken when it is merely idle is the failure mode this design keeps
        # meeting.
        asleep = await _is_asleep(base_url)
        if asleep:
            woken = await _wake(base_url)
            if woken:
                return {
                    "error": "Your assistant was asleep and is starting now — it takes about 30 seconds. Ask again shortly.",
                    "state": "waking",
                    "thread": context_id,
                }
            return {
                "error": (
                    "Your assistant is stopped and this deployment has no way to start it "
                    "automatically. An administrator can start it."
                ),
                "state": "stopped",
                "thread": context_id,
            }
        log.warning("resident_agent: %s", exc)
        return {"error": f"The assistant could not be reached: {exc}", "thread": context_id}

    state = str((task.get("status") or {}).get("state") or "unknown")
    answer = answer_of(task)

    if state != "completed":
        # input-required and failed are both real outcomes and neither is an
        # answer. Reporting the state lets the model say what happened instead
        # of presenting a fragment as the reply.
        return {
            "error": f"The assistant did not finish (state: {state}).",
            "partial": answer or None,
            "thread": context_id,
            "task_id": task.get("id"),
        }

    return {"answer": answer, "thread": context_id, "task_id": task.get("id")}
