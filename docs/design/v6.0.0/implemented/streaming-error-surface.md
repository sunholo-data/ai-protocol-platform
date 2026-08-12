# Streaming Error Surface

**Status**: Implemented
**Priority**: P1 (High)
**Estimated**: 1 day
**Scope**: Frontend
**Dependencies**: [Streaming & Protocols](implemented/streaming-and-protocols.md)
**Created**: 2026-04-23
**Last Updated**: 2026-04-23

## Problem Statement

When an AG-UI stream fails — due to a backend 500, a mid-stream exception, a proxy timeout, or a network drop — the frontend silently hangs. The user sees the loading spinner freeze, the input stays disabled, and nothing ever appears in the chat. The error exists only in the browser's web console.

**Concrete example (from prod):**

```
POST /api/proxy/api/skill/<id>/stream → HTTP 500
"Agent execution failed: Error: HTTP 500: Internal Server Error"
↳ logged to console only
↳ UI: spinner frozen, input disabled, no message rendered
```

**Root cause chain:**

1. `AGUIProvider` builds an `HttpAgent` with no error callback configuration.
2. `useSkillAgent.sendMessage` calls `agent.runAgent()` in a `try/finally` that resets `isLoading` but never sets an error state.
3. The `agent.subscribe()` subscription has `onRunFailed` wired to `setIsLoading(false)` but no error capture.
4. `ChatShell` in `page.tsx` consumes `{ messages, sendMessage, isLoading, stop }` — there is no `error` field.
5. Even if `onRunFailed` fired with an error payload, there is nowhere to display it.

**Affected failure modes:**

| Failure | Where caught today | User sees |
|---|---|---|
| HTTP 4xx (auth expired, skill not found) | `@ag-ui/client` throws | Nothing |
| HTTP 5xx (backend crash) | `@ag-ui/client` throws | Nothing |
| Mid-stream `RUN_ERROR` event | `onRunFailed` fires | Nothing |
| Network drop / proxy timeout | `fetch` rejects | Nothing |
| Stream hang (no events, no close) | Never caught | Frozen spinner forever |

**Impact:** Users submit a message, wait, see nothing change, and re-submit — potentially sending duplicate requests into a broken backend.

## Goals

**Primary Goal:** Every streaming failure produces a visible, actionable inline error message in the chat before the conversation returns to idle state.

**Success Metrics:**
- HTTP 4xx/5xx: inline error renders within 200ms of failure response
- `RUN_ERROR` event: inline error renders on the event, stream terminates cleanly
- Network drop / proxy timeout: inline error renders within 5s (connect timeout) or at stream abort
- Zero frozen-spinner states: `isLoading` always returns to `false` on any failure path
- Retry affordance: user can resend the last message with one click from the error state

**Non-Goals:**
- Fixing the underlying backend errors that cause 500s (this doc is about surfacing, not prevention)
- Retry logic with exponential backoff (retry is one click, not automatic)
- Error aggregation / error history across sessions
- Channel-level error handling (Telegram, email — separate concern)

## Axiom Alignment

| # | Axiom | Score | Notes |
|---|-------|-------|-------|
| 1 | INSTANT FEEL | +1 | Error surfaces immediately instead of leaving a frozen spinner; perceived responsiveness improves even in failure |
| 2 | EARNED TRUST | +1 | Honest failure messaging beats silent hang — users can trust the system to tell them when it's broken |
| 3 | SKILLS, NOT FEATURES | 0 | Infrastructure change invisible to skill authors |
| 4 | RIGHT MODEL, RIGHT MOMENT | 0 | No model routing impact |
| 5 | GRACEFUL DEGRADATION | +1 | Explicit goal: every failure path falls to a usable state rather than a broken one |
| 6 | PROTOCOL OVER CUSTOM | +1 | Consumes the AG-UI `RUN_ERROR` event and `onRunFailed` callback already in the protocol; no custom error format invented |
| 7 | API FIRST | 0 | Frontend-only change; backend API surface unchanged |
| 8 | OBSERVABLE BY DEFAULT | +1 | Error state logged with structured metadata (skill ID, HTTP status, message) for Cloud Logging correlation |
| 9 | SECURE BY CONSTRUCTION | 0 | Error messages show user-friendly text; raw stack traces from the backend never forwarded to the browser |
| 10 | THIN CLIENT, FAT PROTOCOL | +1 | Error classification lives in the hook, not in the component; rendering stays a thin display layer |
| | **Net Score** | **+6** | Threshold: >= +4 |

## Design

### Error Taxonomy

Three distinct failure categories, each with a different capture point:

```
Category A — Pre-stream HTTP errors
  Source: @ag-ui/client throws when fetch() gets a non-2xx status
  Capture: try/catch around agent.runAgent()
  Examples: HTTP 500, HTTP 401, HTTP 404, HTTP 502 (proxy unreachable)

Category B — In-stream RUN_ERROR events
  Source: backend emits RUN_ERROR in the SSE stream
  Capture: agent.subscribe({ onRunFailed }) callback
  Examples: ADK agent exception mid-run, tool execution failure, context overflow

Category C — Network / timeout failures
  Source: fetch() rejects or stream aborts (NetworkError, AbortError)
  Capture: try/catch around agent.runAgent() + watchdog timer
  Examples: backend unreachable, Cloud Run cold-start timeout, proxy timeout
```

**User-visible message per category:**

| Category | Default message | Retry? |
|---|---|---|
| HTTP 401 | "Session expired — please refresh the page" | No (page refresh) |
| HTTP 404 | "Skill not found" | No |
| HTTP 5xx | "Something went wrong on our end. Try again." | Yes |
| HTTP 502 | "Can't reach the server. Try again." | Yes |
| `RUN_ERROR` | "The agent encountered an error. Try again." | Yes |
| Network / timeout | "Connection lost. Try again." | Yes |

Raw server error messages and stack traces are **never** shown to the user. The raw error is logged to the browser console (existing behaviour) and to Cloud Logging (new: structured log in the hook).

### Frontend State Machine Changes

**`useSkillAgent` return type — add `error` field:**

```typescript
export interface StreamError {
  kind: "http" | "run_error" | "network";
  status?: number;       // HTTP status code if applicable
  message: string;       // user-visible message
  retryable: boolean;
  rawMessage: string;    // console/logging only, never rendered
}

export interface UseSkillAgentReturn {
  messages: SkillMessage[];
  sendMessage: (text: string) => Promise<void>;
  isLoading: boolean;
  error: StreamError | null;   // NEW
  clearError: () => void;      // NEW — call on retry or dismiss
  stop: () => void;
}
```

**State transitions:**

```
idle
  ↓ sendMessage()
loading
  ↓ RUN_STARTED event  → still loading
  ↓ TEXT_MESSAGE_* events → messages update
  ↓ RUN_FINISHED → idle  (success path, existing)
  ↓ HTTP error throw → error  (category A/C)
  ↓ onRunFailed(event) → error  (category B)
  ↓ stop() → idle  (user abort, existing)

error
  ↓ clearError() + sendMessage() → loading  (retry)
  ↓ clearError() → idle  (dismiss)
```

**`sendMessage` changes in `useSkillAgent`:**

```typescript
const sendMessage = useCallback(async (text: string) => {
  clearError();
  agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text } as Message);
  setIsLoading(true);
  try {
    await agent.runAgent();
  } catch (err) {
    const streamErr = classifyError(err);
    logger.warn("stream_error", { skillId, ...streamErr });
    setError(streamErr);
  } finally {
    setIsLoading(false);
  }
}, [agent, skillId, clearError]);
```

**`onRunFailed` subscription change:**

```typescript
const sub = agent.subscribe({
  onMessagesChanged: () => sync(),
  onRunStartedEvent: () => setIsLoading(true),
  onRunFinalized: () => setIsLoading(false),
  onRunFailed: (event) => {
    const streamErr = classifyRunError(event);
    logger.warn("stream_run_failed", { skillId, ...streamErr });
    setError(streamErr);
    setIsLoading(false);
  },
});
```

**`classifyError` helper (in `useSkillAgent`):**

```typescript
function classifyError(err: unknown): StreamError {
  const msg = err instanceof Error ? err.message : String(err);
  const httpMatch = msg.match(/HTTP (\d+)/);
  if (httpMatch) {
    const status = parseInt(httpMatch[1]);
    if (status === 401) return { kind: "http", status, message: "Session expired — please refresh the page", retryable: false, rawMessage: msg };
    if (status === 404) return { kind: "http", status, message: "Skill not found", retryable: false, rawMessage: msg };
    if (status >= 500) return { kind: "http", status, message: "Something went wrong on our end. Try again.", retryable: true, rawMessage: msg };
    return { kind: "http", status, message: "Request failed. Try again.", retryable: true, rawMessage: msg };
  }
  return { kind: "network", message: "Connection lost. Try again.", retryable: true, rawMessage: msg };
}
```

### UI Component Changes

**`ChatShell` in `page.tsx` — inline error banner:**

The error renders as an inline message in the chat scroll area, positioned after the last user message (not a modal or toast — it stays in conversational context). Visually matches the assistant message style with a warning colour treatment.

```
┌──────────────────────────────────────────────┐
│ User: "What's the status of invoice #123?"   │  ← user bubble (right-aligned)
│                                               │
│ ⚠ Something went wrong on our end.           │  ← error message (left-aligned)
│   [Try again]  [Dismiss]                     │  ← inline action buttons
└──────────────────────────────────────────────┘
```

The page stores `lastUserMessage` in a `useRef` so "Try again" can resend without the user retyping.

**Input field disabled state:** currently disabled during `isLoading`. Change: also disabled during `error` state — user must explicitly retry or dismiss before sending a new message (prevents accidental double-send while in error).

### Stream Hang Detection (timeout)

`@ag-ui/client` does not implement a first-event timeout. A stream that opens with HTTP 200 response headers but never sends the first SSE event hangs indefinitely.

Add a 30-second watchdog in `useSkillAgent`:

```typescript
useEffect(() => {
  if (!isLoading) return;
  const timer = setTimeout(() => {
    agent.abortRun();
    setError({ kind: "network", message: "Connection lost. Try again.", retryable: true, rawMessage: "stream_hang_timeout_30s" });
    setIsLoading(false);
  }, 30_000);
  // Cancel watchdog on first event (onRunStartedEvent fires) or on completion.
  return () => clearTimeout(timer);
}, [isLoading]);
```

`onRunStartedEvent` already calls `setIsLoading(true)` — since `isLoading` was already `true`, the effect does not re-fire. The watchdog effect should instead key off a separate `runStarted` boolean that flips to `true` on `onRunStartedEvent`, so the `useEffect` dep array can distinguish "loading, not yet started" from "loading, started".

### Backend Changes

None required. The backend already emits `RUN_ERROR` events via `ag-ui-adk` when an ADK exception propagates. HTTP 500 responses are already generated by FastAPI's exception handlers. This design is a frontend surfacing change only.

**Optional follow-up (not in scope):** Add structured error bodies to backend 5xx responses (`{ "error": "agent_exception", "detail": "..." }`). The proxy already passes response bodies through, and the frontend could use `detail` for richer Cloud Logging entries. Track as a separate P2 task.

### Logging

Each error path calls a structured logger (the existing `logger` utility already used in the frontend):

```typescript
logger.warn("stream_error", {
  skillId,
  kind: streamErr.kind,
  status: streamErr.status,
  rawMessage: streamErr.rawMessage,  // stack traces here for Cloud Logging, not rendered
});
```

This appears in Cloud Logging as structured JSON under the v6 frontend Cloud Run service, correlatable with backend traces by `skillId` and timestamp.

## API Changes

No backend API changes. The only TypeScript interface change is `UseSkillAgentReturn` — additive, so existing consumers compile unchanged.

## Migration

No data migration. No feature flags. The `error` and `clearError` fields are additive to `UseSkillAgentReturn`. The only consumer today is `ChatShell` in `frontend/src/app/chat/[skillId]/page.tsx`.

## Testing Strategy

### Frontend Unit Tests (Vitest)

**`useSkillAgent` hook** (`frontend/src/hooks/__tests__/useSkillAgent.test.ts`):

- `agent.runAgent()` rejects with `"HTTP 500: ..."` → `error.kind === "http"`, `error.status === 500`, `error.retryable === true`, `isLoading === false`
- `agent.runAgent()` rejects with `"HTTP 401: ..."` → `error.retryable === false`
- `agent.runAgent()` rejects with `"HTTP 502: ..."` → `error.message` contains "Can't reach the server"
- `onRunFailed` callback fired → `error.kind === "run_error"`, `isLoading === false`
- `agent.runAgent()` rejects with `TypeError: fetch failed` → `error.kind === "network"`
- `clearError()` resets `error` to `null`
- `sendMessage()` calls `clearError()` before running (clears previous error state)
- Hang timeout: mock `setTimeout` elapses with no `runStarted` → error set, `abortRun()` called, `isLoading === false`

**`ChatShell` component** (`frontend/src/__tests__/chat-error-display.test.tsx`):

- When `error` is non-null and `retryable`: error message renders, "Try again" button present, "Dismiss" button present
- When `error` is non-null and `!retryable`: error message renders, only "Dismiss" button (no "Try again")
- "Try again" calls `sendMessage` with the stored last user message text
- "Dismiss" calls `clearError` without calling `sendMessage`
- During `error` state: input is `disabled`
- When `error` is null: no error UI rendered

### Manual Test Checklist

- [ ] Kill backend process mid-stream → error banner appears, retry resends and succeeds
- [ ] Backend returns 500 → "Something went wrong" + "Try again" appears inline
- [ ] Backend returns 401 (expired token) → "Session expired" message, no retry button
- [ ] Network down → "Connection lost. Try again." appears within 5s
- [ ] Hang (no first SSE event for 30s) → timeout error appears, spinner gone, input re-enabled
- [ ] Retry after error → conversation resumes correctly
- [ ] Dismiss after error → blank input enabled, no error UI, can send new message

## Success Criteria

- [ ] `useSkillAgent` exposes `error: StreamError | null` and `clearError: () => void`
- [ ] All five failure modes (HTTP 4xx, 5xx, RUN_ERROR, network, hang) produce an inline error message in the chat UI
- [ ] `isLoading` returns to `false` on every failure path — zero frozen-spinner states
- [ ] Retry button resends the last user message without retyping
- [ ] Raw stack traces never appear in the browser DOM
- [ ] Hook unit tests pass (`cd frontend && npm run test:run`)
- [ ] Quality check passes (`cd frontend && npm run quality:check:fast`)

## Related Documents

- [Streaming & Protocols](implemented/streaming-and-protocols.md) — AG-UI integration design
- [Frontend Architecture](implemented/frontend-architecture.md) — hook and component conventions
- [local-dev-cli.md](../../v6.1.0/local-dev-cli.md) — CLI surface (not applicable for this feature)

---

## Implementation Report

**Completed**: 2026-04-23
**Actual Effort**: [e.g., 5 days vs 3 estimated]
**Branch/PR**: [link or commit range]

### What Was Built
- [Summary of actual implementation]
- [Any deviations from plan]

### Files Changed
- [New files created]
- [Modified files]

### Lessons Learned
- [What went well]
- [What could be improved]
