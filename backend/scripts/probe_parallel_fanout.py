"""M0 viability probe for docs/design/v6.31.0/parallel-skill-fanout.md.

Question: does the MODEL emit N AgentTool calls in ONE response (so ADK's
asyncio.gather in flows/llm_flows/functions.py actually overlaps them), or does
it call one per turn (in which case the fan-out serialises and the design must
drive N deterministically instead)?

Method: a parent agent whose only tool is an AgentTool over a deliberately slow
child. Ask for 3 narrow objectives. Compare total branch wall-clock against the
sum of the individual branch durations.

Run from backend/:  uv run python <path>/m0_probe.py --model gemini-2.5-flash
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool, FunctionTool
from google.genai import types

from adk.agent import resolve_model
from observability.timing import (
    LatencyTracker,
    get_current_tracker,
    reset_current_tracker,
    set_current_tracker,
)

BRANCH_SECONDS = 6.0
APP = "m0_probe"

# (tool_name, phase, monotonic_ts) — appended from the tool callbacks, which
# run inside the gathered tasks, so overlap here IS the concurrency evidence.
marks: list[tuple[str, str, float]] = []
T0 = time.monotonic()

# M2 emission evidence: what the per-branch callbacks saw, and when a
# stream_agui_events-style pump actually got it onto the wire.
tracker_seen: list[str] = []
drained_at: list[tuple[float, str]] = []


async def slow_lookup(topic: str) -> str:
    """Look up authoritative reference information about one topic.

    Args:
        topic: the single narrow topic to look up.
    """
    await asyncio.sleep(BRANCH_SECONDS)
    return f"REFERENCE FINDINGS on {topic}: finding-A; finding-B; finding-C."


def build_parent(model_ref: str) -> LlmAgent:
    worker = LlmAgent(
        name="research_worker",
        model=resolve_model(model_ref),
        description="Researches ONE narrow objective and returns a short summary.",
        instruction=(
            "You research exactly ONE narrow objective given to you as `request`. "
            "Call `slow_lookup` exactly once with that objective as the topic, then "
            "reply with two sentences summarising what it returned. Never ask "
            "clarifying questions."
        ),
        tools=[FunctionTool(slow_lookup)],
    )

    def _emit_branch(phase: str, args: dict[str, Any]) -> None:
        """Emit a live branch update the way M2 would — from the PARENT's tool
        callback, which is the only legal seam (backend/adk/CLAUDE.md trap 1:
        emitting from inside the sub-agent's own callbacks hits the NULL
        tracker, because AgentTool runs it in a separate Runner)."""
        tracker = get_current_tracker()
        tracker_seen.append(f"{phase}:{type(tracker).__name__}")
        objective = str(args.get("request", ""))[:48]
        tracker.emit_a2ui_surface(
            surface_id="fanout_board",
            messages=[{"probe": phase, "objective": objective}],
            source_id=f"{phase}:{objective}",
            artifact={"kind": "fanout", "title": "Research fan-out"},
        )

    def _before(tool: Any, args: dict[str, Any], tool_context: Any) -> None:
        marks.append((tool.name, f"start::{str(args)[:60]}", time.monotonic() - T0))
        _emit_branch("start", args)
        return None

    def _after(tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any) -> None:
        marks.append((tool.name, f"end::{str(args)[:60]}", time.monotonic() - T0))
        _emit_branch("end", args)
        return None

    return LlmAgent(
        name="fanout_parent",
        model=resolve_model(model_ref),
        instruction=(
            "You answer a broad question by decomposing it into narrow objectives "
            "and delegating each one.\n"
            "PROCEDURE (follow exactly):\n"
            "1. Split the user's request into its distinct objectives.\n"
            "2. In a SINGLE response, call the `research_worker` tool ONCE PER "
            "OBJECTIVE — issue all of those calls together, do not wait for one "
            "to come back before issuing the next.\n"
            "3. When all results are in, write one merged brief, attributing each "
            "section to the objective that produced it.\n"
            "Never call research_worker more than once for the same objective."
        ),
        tools=[AgentTool(agent=worker)],
        before_tool_callback=_before,
        after_tool_callback=_after,
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument(
        "--message",
        default=(
            "I need a briefing covering three separate topics: "
            "(1) the 2026 outlook for Iberian solar PPAs, "
            "(2) the state of battery storage (BESS) in Spain, and "
            "(3) onshore wind permitting delays in Spain. "
            "Research each one and then give me one merged brief."
        ),
    )
    args = ap.parse_args()

    parent = build_parent(args.model)
    runner = Runner(
        app_name=APP,
        agent=parent,
        session_service=InMemorySessionService(),
    )
    session = await runner.session_service.create_session(app_name=APP, user_id="m0")

    llm_responses_with_calls: list[int] = []
    event_log: list[str] = []
    final_text = ""
    t_start = time.monotonic()

    # Mirror fast_api_app.stream_skill: bind a per-request tracker to the async
    # context. The question this answers is whether that binding is still
    # visible inside the tasks ADK gathers for the parallel calls.
    tracker = LatencyTracker(skill_id="probe", session_id=session.id, user_id="m0")
    token = set_current_tracker(tracker)

    async def _pump() -> None:
        """Stand-in for stream_agui_events' 1s drain timer, which flushes
        queued surface events independently of ADK's event cadence."""
        while True:
            await asyncio.sleep(0.5)
            for ev in tracker.drain_stage_events():
                val = getattr(ev, "value", {}) or {}
                msgs = val.get("messages") or [{}]
                drained_at.append((time.monotonic() - t_start, str(msgs[0])[:60]))

    pump = asyncio.create_task(_pump())

    async for event in runner.run_async(
        user_id="m0",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part.from_text(text=args.message)]),
    ):
        parts = getattr(getattr(event, "content", None), "parts", None) or []
        _fc = [p.function_call.name for p in parts if getattr(p, "function_call", None)]
        _fr = [p.function_response.name for p in parts if getattr(p, "function_response", None)]
        _tx = " ".join(p.text for p in parts if getattr(p, "text", None))[:70].replace("\n", " ")
        event_log.append(
            f"{time.monotonic() - t_start:7.2f}s author={getattr(event, 'author', '?'):16s} "
            f"calls={_fc} responses={_fr} text={_tx!r}"
        )
        n_calls = len(_fc)
        if n_calls:
            llm_responses_with_calls.append(n_calls)
        if event.is_final_response() and parts:
            final_text = "\n".join(p.text for p in parts if getattr(p, "text", None))

    pump.cancel()
    reset_current_tracker(token)
    total = time.monotonic() - t_start

    # Pair start/end marks for the AgentTool branches only.
    branches: dict[str, dict[str, float]] = {}
    for name, phase, ts in marks:
        if name != "research_worker":
            continue
        kind, _, key = phase.partition("::")
        branches.setdefault(key, {})[kind] = ts
    durations = [b["end"] - b["start"] for b in branches.values() if "start" in b and "end" in b]
    span = (
        max(b["end"] for b in branches.values() if "end" in b)
        - min(b["start"] for b in branches.values() if "start" in b)
        if durations
        else 0.0
    )

    print("\n" + "=" * 72)
    print(f"MODEL                     : {args.model}")
    print(f"branch sleep              : {BRANCH_SECONDS}s")
    print(
        f"LLM responses w/ calls    : {llm_responses_with_calls}  "
        f"(a single entry of 3 == batched; [1,1,1] == serialised)"
    )
    print(f"branches observed         : {len(durations)}")
    print(f"per-branch durations      : {[round(d, 2) for d in durations]}")
    print(f"SUM of branch durations   : {round(sum(durations), 2)}s")
    print(f"SPAN first-start->last-end: {round(span, 2)}s")
    if durations:
        ratio = sum(durations) / span if span else 0
        print(f"overlap factor            : {round(ratio, 2)}x ({'CONCURRENT' if ratio > 1.5 else 'SERIALISED'})")
    print(f"total turn wall-clock     : {round(total, 2)}s")
    print("=" * 72)
    print("\n--- M2 emission seam (parent tool callbacks) ---")
    nulls = [t for t in tracker_seen if "Null" in t]
    print(f"  tracker inside gathered branch tasks : {sorted(set(tracker_seen))}")
    print(
        f"  NULL-tracker hits (silent no-ops)    : {len(nulls)} "
        f"({'OK' if not nulls else 'BROKEN — emissions would vanish'})"
    )
    print(f"  surface events that reached the wire : {len(drained_at)}")
    for ts, what in drained_at:
        print(f"      {ts:7.2f}s  {what}")

    print("\n--- PARENT event stream (what a user could be shown) ---")
    for line in event_log:
        print("  " + line)
    print("\n--- branch timeline (s from start) ---")
    for name, phase, ts in marks:
        print(f"  {ts:7.2f}  {name:18s} {phase}")
    print("\n--- final answer (first 600 chars) ---")
    print(final_text[:600])


if __name__ == "__main__":
    asyncio.run(main())
