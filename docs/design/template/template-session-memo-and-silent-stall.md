# One session fetch per turn, and silence is a failure too

**Status**: ✅ SHIPPED upstream 2026-09-22 (ported from the CPH Uni AIPLA fork, measured on its prod 2026-09-21)
**Priority**: P1 — every ADK + ag_ui_adk + Agent Engine deployment pays this on every turn
**Scope**: `backend/adk/session_memo.py` (new) · `adk/session.py` (wrap at the factory seam) · `adk/agui.py` (open around the run, drain at the end) · `adk/model_errors.py` (`SilentStallError`, `MODEL_SILENT_STALL`) · `adk/resilient_llm.py` (first-token deadline on streamed calls)
**Source**: AIPLA fork `docs/design/v6.1.0/implemented/ttft-optimization.md` § "Revisited on prod, 2026-09-21" — the ttft tracker's per-stage marks (M1 of that sprint) plus Cloud Trace on the same turns

## What was measured

Thirty days of the fork's prod `ttft` rows (Cloud Run europe-north1, Agent Engine
sessions europe-west1, gemini-3.5-flash-lite, thinking off):

| stage | p50 | p90 |
|---|---|---|
| request → our `before_agent` fires | **2.8 s** | 3.9 s |
| `before_model_done` → first model token | 1.0 s | ~4 s |
| **student-perceived first token** | **4.8 s** | 8.6 s |

The 2.8 s was flat regardless of session length. Cloud Trace put only ~0.7 s of
it inside ADK's `invocation` span; the rest sat before it. Per turn, on the
SAME session, at ~0.6 s each (a `get_session` is two HTTP calls — get + list
events):

1. ag_ui_adk's cache-hit check `get_session`
2. ag_ui_adk's `pending_tool_calls` state read (another `get_session`)
3. the Runner's own `get_session`
4. the Runner's `append_event` of the user's message — awaited before the agent starts
5. the before-agent state-delta `append_event` — awaited before the model is asked

Separately, the tail: six turns in thirty days at **48–105 s to first token**,
each a request logged as sent and then silent — no error, no 429, nothing for
`ResilientLlm`'s classifier to see, so neither retry nor fallback ever fired.
The client had abandoned the stream at 30 s. The turns either side ran in 1–3 s.

## Design

### `ReadMemoSessionService` — a memo, not a cache

Wraps any `BaseSessionService`, applied once at `get_session_service()` around
`ResilientVertexSessionService`. Inside a request (`session_read_memo()` open —
`stream_agui_events` opens it around the run and closes it in its `finally`):

- **Reads are memoised** per `(app_name, user_id, session_id)`. Safe because
  ADK mutates the `Session` object in place — `append_event` appends to
  `session.events` and applies the state delta on that object before writing
  through — so every later reader holds what the first reader's object has
  become. A `GetSessionConfig` read is a different view and bypasses it.
- **Writes apply in memory now and go to the wire in the background, chained
  in order.** The inner service is handed a shadow copy (own `events`, own
  `state`) so its own in-memory half does not append to the real object a
  second time; the wire half reads nothing from the session but `app_name`
  and `id`. Order on the wire is the chain: each write awaits the previous.
- **`drain_pending_session_writes()`** runs in the stream's `finally`, before
  the response ends — Cloud Run throttles the CPU after that.

Outside a request the wrapper is transparent. Nothing outlives the request; a
second request for the same session on the same instance re-fetches. This is
deliberately narrower than the "cache + write-back" pattern rejected in
`v6.1.0/implemented/ttft-optimization.md`.

**The trade, stated once:** a failed background write is logged at ERROR with
the ids, not raised. `ResilientVertexSessionService` (issue #30) retries and
then re-raises "so ADK sees it"; under this wrapper that re-raise ends in the
drain's log line rather than failing a turn whose reply is already on screen.
Never silent — no longer fatal. Do not wrap a store whose write failure must
abort the turn.

**Why backgrounding only the user's message was not enough** (measured on the
fork's dev, first cut): setup gap 3.05 → 1.5 s but the callbacks window 0.37 →
0.9 s — the before-agent state-delta append drained the user write and then
paid its own. The 0.6 s moved. Hence the chain, for every in-request write.

### `SilentStallError` — the failure the classifier could not see

`ResilientLlm` bounds the FIRST chunk of a streamed call at
`FIRST_TOKEN_DEADLINE_S` (10 s; env `MODEL_FIRST_TOKEN_DEADLINE_S`, `0`
disables). On timeout it closes the abandoned generator and raises
`SilentStallError`, which `classify` maps to `MODEL_SILENT_STALL` — transient
and fallbackable — so the existing loop does the rest: retry the member (bounded
by `max_retries_per_model`), then fall back down the chain, with `MODEL_RETRY` /
`MODEL_FALLBACK` events on the wire as for any other transient. It fires only
before any output (any chunk, thoughts included — a thinking model is alive),
never on a slow tail, and never on a non-streamed call (its one chunk IS the
answer).

10 s is the measured gap in a bimodal tail — flash-lite on Vertex: 108/114
turns under 6.1 s, then 14.9 s and up — and leaves a retry room to land before
a 30 s client hang-timeout. A fork on a slower model should raise it via the env.

## Result

Measured on the fork's dev (warm turns) after the port's two halves:

| | before | after |
|---|---|---|
| request → callbacks fire | 3.05 s | 1.2–2.1 s (includes `create_session` on a new thread) |
| callbacks → model asked | 0.37 s | **0.02 s** |
| model asked → first token | 0.77 s | 0.55–0.95 s |
| **first token** | **4.2 s** | **2.0–2.2 s** |

What remains is one `get_session` (~0.6 s) — Agent Engine's per-call cost —
and the model's own ~1 s. Removing the former is a same-region session store,
a different design.

## Tests

- `tests/unit/test_session_memo.py` — same object across reads; nothing survives
  the request; a task created inside the request shares the memo; writes apply
  in memory and land in order without blocking; the real object is never
  appended to twice; partials pass straight through; a failed write is logged,
  not raised, and does not stop the next; the stream opens and drains.
- `tests/unit/test_resilient_llm.py` § 7 — a stalled first attempt is retried
  and the abandoned generator closed; a persistent stall falls back; no fallback
  left → typed `ModelTurnError(MODEL_SILENT_STALL)`; the deadline never applies
  once output has started, treats a thought as life, and never bounds a
  non-streamed call.
- `tests/unit/test_session_factories.py` — the factory contract now checks the
  Vertex service *underneath* the memo.
