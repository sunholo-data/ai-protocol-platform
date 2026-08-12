import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Build a minimal fake AbstractAgent with just the methods the hook touches.
// Keeping it explicit (not extending real HttpAgent) means we don't have to
// stub a working HTTP transport just to assert hook wiring.
class FakeAgent {
  messages: Array<{ id: string; role: string; content: string }> = [];
  subscribers: Array<Record<string, (params: unknown) => void>> = [];
  runAgent = vi.fn(async () => ({ newMessages: [] }));
  abortRun = vi.fn();

  subscribe(s: Record<string, (params: unknown) => void>) {
    this.subscribers.push(s);
    return {
      unsubscribe: () => {
        this.subscribers = this.subscribers.filter((x) => x !== s);
      },
    };
  }

  addMessage(m: { id: string; role: string; content: string }) {
    this.messages.push(m);
    this.subscribers.forEach((s) => s.onMessagesChanged?.({}));
  }

  emitRunStart() {
    this.subscribers.forEach((s) => s.onRunStartedEvent?.({}));
  }

  // A clean RUN_FINISHED terminal (distinct from onRunFinalized, the lifecycle end).
  emitRunFinished() {
    this.subscribers.forEach((s) => s.onRunFinishedEvent?.({ event: {} }));
  }

  emitRunFinal() {
    this.subscribers.forEach((s) => s.onRunFinalized?.({}));
  }

  emitRunFailed(event: unknown = {}) {
    this.subscribers.forEach((s) => s.onRunFailed?.(event));
  }

  // A backend-emitted RUN_ERROR *event* (429s, tool failures, BUDGET_EXCEEDED)
  // — the AG-UI client dispatches these to onRunErrorEvent as `{ event }`,
  // NOT onRunFailed. Classification (budget/rate-limit/run_error) lives there.
  emitRunError(event: unknown = {}) {
    this.subscribers.forEach((s) => s.onRunErrorEvent?.({ event }));
  }

  emitReasoningStart() {
    this.subscribers.forEach((s) => s.onReasoningStartEvent?.({}));
  }

  emitReasoningContent(reasoningMessageBuffer: string) {
    this.subscribers.forEach((s) =>
      s.onReasoningMessageContentEvent?.({ reasoningMessageBuffer }),
    );
  }

  emitReasoningEnd() {
    this.subscribers.forEach((s) => s.onReasoningEndEvent?.({}));
  }

  emitCustomEvent(name: string, value: unknown) {
    this.subscribers.forEach((s) =>
      s.onCustomEvent?.({ event: { name, value } }),
    );
  }

  emitTextMessageStart(messageId?: string) {
    this.subscribers.forEach((s) =>
      s.onTextMessageStartEvent?.({ event: { messageId } }),
    );
  }

  emitToolCallStart(toolCallId: string, toolCallName: string, parentMessageId?: string) {
    this.subscribers.forEach((s) =>
      s.onToolCallStartEvent?.({ event: { toolCallId, toolCallName, parentMessageId } }),
    );
  }

  emitToolCallArgs(toolCallId: string, delta: string) {
    this.subscribers.forEach((s) =>
      s.onToolCallArgsEvent?.({ event: { toolCallId, delta } }),
    );
  }

  emitToolCallEnd(toolCallId: string) {
    this.subscribers.forEach((s) =>
      s.onToolCallEndEvent?.({ event: { toolCallId } }),
    );
  }
}

const fake = new FakeAgent();

// Swappable agent reference. Most tests use `fake` directly. D2
// (chat-history-deep-fixes H2) reassigns `currentAgent` to simulate
// AGUIProvider rebuilding the HttpAgent on sessionId change — that
// triggers the hook's useEffect([agent]) and exercises the
// agent-identity reset path.
let currentAgent: FakeAgent = fake;

vi.mock("@/providers/AGUIProvider", () => ({
  useAGUIAgent: () => currentAgent,
  AGUIProvider: ({ children }: { children: ReactNode }) => children,
}));

// Sprint 2.10: mock useOptionalSurfaceRegistry so tests can control
// the snapshot returned at sendMessage time without spinning up the
// full SurfaceRegistry + v0.9 SDK. The real registry is covered by
// SurfaceRegistry.test.tsx.
let surfaceSnapshotStub: Record<string, { catalogId: string; dataModel: unknown }> = {};
// Shared mock so the A2UI_SURFACE test can assert appendMessages was called.
const { appendMessagesMock } = vi.hoisted(() => ({ appendMessagesMock: vi.fn() }));
vi.mock("@/providers/SurfaceRegistry", () => ({
  useOptionalSurfaceRegistry: () => ({
    readA2uiSurfaceState: () => surfaceSnapshotStub,
    // Other API surface stubs (not read by useSkillAgent):
    register: vi.fn(),
    unregister: vi.fn(),
    getMount: vi.fn(),
    getPolicy: vi.fn(),
    appendMessages: appendMessagesMock,
    clearSurface: vi.fn(),
    clearByPersistence: vi.fn(),
    getState: vi.fn(),
  }),
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";

beforeEach(() => {
  currentAgent = fake;
  fake.messages = [];
  fake.runAgent.mockReset();
  fake.abortRun.mockReset();
  fake.runAgent.mockResolvedValue({ newMessages: [] });
});

describe("useSkillAgent — core", () => {
  it("returns the documented public surface", () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(Object.keys(result.current).sort()).toEqual([
      "clearError",
      // COMPACTION-WIRE M4 — history compactions, rendered in Activity.
      "compactions",
      "delegations",
      "error",
      "fallbacks",
      "isLoading",
      "isThinking",
      "messages",
      "resolvedModel",
      "sendMessage",
      "sessionId",
      "stageLabel",
      "stop",
      "thinkingContent",
      // COMPACTION-LATENCY M2 — composer released when the ANSWER is done.
      "tidyingUp",
      "toolCalls",
    ]);
  });

  it("syncs messages from the agent and sends new user messages", async () => {
    const { result } = renderHook(() => useSkillAgent());

    expect(result.current.messages).toEqual([]);

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]).toMatchObject({ role: "user", content: "hello" });
    });
    expect(fake.runAgent).toHaveBeenCalled();
  });

  it("auto-retries once on an EMPTY_RUN and surfaces no error when the retry succeeds", async () => {
    const { result } = renderHook(() => useSkillAgent());
    // First run comes back empty; the retry succeeds silently.
    fake.runAgent
      .mockImplementationOnce(async () => {
        fake.emitRunError({ code: "EMPTY_RUN" });
        return { newMessages: [] };
      })
      .mockImplementationOnce(async () => ({ newMessages: [] }));

    await act(async () => {
      await result.current.sendMessage("hi");
    });

    expect(fake.runAgent).toHaveBeenCalledTimes(2); // one auto-retry
    expect(result.current.error).toBeNull(); // never shown to the user
  });

  // NEVER SILENT — a truncated stream (instance replaced / proxy ECONNRESET /
  // network drop) ends with no terminal event. It must never leave a dead turn.
  it("surfaces an error when the run ends with NO terminal event (truncated stream)", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitRunFinal()); // finalized without RUN_FINISHED / RUN_ERROR
    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.rawMessage).toBe("stream_truncated_no_terminal");
    expect(result.current.error?.retryable).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });

  it("stays silent-free but error-free on a CLEAN finish (RUN_FINISHED seen)", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitRunFinished());
    act(() => fake.emitRunFinal());
    expect(result.current.error).toBeNull(); // no false truncation error
  });

  it("does not double-report: a RUN_ERROR terminal suppresses the truncation guard", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitRunError({ code: "MODEL_UNAVAILABLE" }));
    act(() => fake.emitRunFinal());
    expect(result.current.error?.rawMessage).not.toBe("stream_truncated_no_terminal");
    expect(result.current.error).not.toBeNull(); // the real error still shows
  });

  it("a user-initiated stop does not raise a truncation error", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => result.current.stop());
    act(() => fake.emitRunFinal());
    expect(result.current.error).toBeNull();
  });

  it("surfaces a retryable error after two consecutive EMPTY_RUNs", async () => {
    const { result } = renderHook(() => useSkillAgent());
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunError({ code: "EMPTY_RUN" });
      return { newMessages: [] };
    });

    await act(async () => {
      await result.current.sendMessage("hi");
    });

    expect(fake.runAgent).toHaveBeenCalledTimes(2); // initial + one retry, then gives up
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.retryable).toBe(true);
  });

  it("tracks isLoading across run lifecycle", async () => {
    const { result } = renderHook(() => useSkillAgent());

    expect(result.current.isLoading).toBe(false);
    act(() => fake.emitRunStart());
    await waitFor(() => expect(result.current.isLoading).toBe(true));
    act(() => fake.emitRunFinal());
    await waitFor(() => expect(result.current.isLoading).toBe(false));
  });

  it("stop delegates to abortRun", () => {
    const { result } = renderHook(() => useSkillAgent());
    result.current.stop();
    expect(fake.abortRun).toHaveBeenCalled();
  });

  // Regression for the multi-surface-A2UI v0.9 demo: Gemini sometimes
  // emits send_a2ui_json_to_client without text — AG-UI then creates an
  // assistant Message with content=undefined. Earlier toSkillMessage
  // dropped that message, so MessageBubble never rendered and the
  // workspace surface stayed empty (the dispatcher lives inside the
  // bubble's render). Tool-only assistant turns must surface as
  // SkillMessage{content:""} so the bubble renders.
  it("preserves assistant messages whose content is undefined (tool-only turn)", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      // Tool-only assistant message — content is omitted at the
      // AG-UI Message level. Cast to bypass the FakeAgent's
      // string-only type signature.
      (fake.messages as Array<{ id: string; role: string; content?: string }>).push({
        id: "asst-tool-only",
        role: "assistant",
      });
      fake.subscribers.forEach((s) => s.onMessagesChanged?.({}));
    });
    await waitFor(() => {
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]).toMatchObject({
        id: "asst-tool-only",
        role: "assistant",
        content: "",
      });
    });
  });
});

// docs/design/v6.1.0/ttft-instrumentation.md M2 — STAGE_PROGRESS labels
// from the backend LatencyTracker decouple perceived TTFT from real
// model TTFT. The hook subscribes to onCustomEvent, filters for
// STAGE_PROGRESS by name, and exposes the latest label as state.
describe("useSkillAgent — STAGE_PROGRESS perceived snappiness", () => {
  it("starts with stageLabel = null", () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(result.current.stageLabel).toBeNull();
  });

  it("updates stageLabel on STAGE_PROGRESS Custom event", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("STAGE_PROGRESS", {
        stage: "before_model_done",
        label: "Thinking…",
        elapsed_ms: 145,
      });
    });
    await waitFor(() => {
      expect(result.current.stageLabel).toBe("Thinking…");
    });
  });

  it("updates resolvedModel on MODEL_RESOLVED (drives the Activity header)", async () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(result.current.resolvedModel).toBeNull();
    act(() => fake.emitCustomEvent("MODEL_RESOLVED", { model: "claude-opus-4-8", routing: "single" }));
    await waitFor(() => expect(result.current.resolvedModel).toBe("claude-opus-4-8"));
    // A malformed event must not blank a good value.
    act(() => fake.emitCustomEvent("MODEL_RESOLVED", { model: "" }));
    expect(result.current.resolvedModel).toBe("claude-opus-4-8");
  });

  // #12: search runs as an AgentTool with no handoff chip — a stage label keeps
  // it visible in the main chat rather than only the Activity tab.
  it("sets 'Searching the library…' when ai_search starts", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitToolCallStart("t1", "ai_search"));
    await waitFor(() => expect(result.current.stageLabel).toBe("Searching the library…"));
  });

  it("sets 'Searching the web…' when web_search_agent starts", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitToolCallStart("t2", "web_search_agent"));
    await waitFor(() => expect(result.current.stageLabel).toBe("Searching the web…"));
  });

  it("sets no stage label for a non-search tool (Activity-only)", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitRunStart());
    act(() => fake.emitToolCallStart("t3", "extract_ppa_clauses"));
    expect(result.current.stageLabel).toBeNull();
  });

  // NOTE: A2UI_SURFACE routing moved OUT of useSkillAgent (which runs above the
  // SurfaceRegistryProvider, so its registry was always null) into
  // `WorkspaceA2uiEventRouter` in ChatShell — see that component + the render-fix
  // (route A2UI_SURFACE from inside the provider). The old tests here were
  // removed; the router path is covered by SurfaceRegistry.test.tsx +
  // A2UISurfaceMount.test.tsx.

  it("ignores Custom events with unrelated names (no false positives)", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("LATENCY_REPORT", { foo: "bar" });
      fake.emitCustomEvent("UNKNOWN_EVENT", { label: "ignored" });
    });
    // Brief wait so any spurious setState would have flushed.
    await waitFor(() => {
      expect(result.current.stageLabel).toBeNull();
    });
  });

  it("clears stageLabel on first TEXT_MESSAGE_START (handoff to streaming bubble)", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("STAGE_PROGRESS", {
        stage: "before_model_done",
        label: "Thinking…",
        elapsed_ms: 145,
      });
    });
    await waitFor(() => expect(result.current.stageLabel).toBe("Thinking…"));

    act(() => fake.emitTextMessageStart());
    await waitFor(() => expect(result.current.stageLabel).toBeNull());
  });

  it("clears stageLabel on RUN_FINISHED so a stale label can't survive between runs", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("STAGE_PROGRESS", {
        stage: "before_model_done",
        label: "Thinking…",
        elapsed_ms: 145,
      });
    });
    await waitFor(() => expect(result.current.stageLabel).toBe("Thinking…"));
    act(() => fake.emitRunFinal());
    await waitFor(() => expect(result.current.stageLabel).toBeNull());
  });

  it("clears stageLabel on RUN_FAILED", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("STAGE_PROGRESS", {
        stage: "before_model_done",
        label: "Thinking…",
        elapsed_ms: 145,
      });
    });
    await waitFor(() => expect(result.current.stageLabel).toBe("Thinking…"));
    act(() => fake.emitRunFailed({ message: "boom" }));
    await waitFor(() => expect(result.current.stageLabel).toBeNull());
  });

  it("optimistic user message is appended synchronously, before runAgent resolves", async () => {
    // Perceived-TTFT contract: by the time the sendMessage promise
    // returns, agent.messages already includes the user message —
    // meaning the next React render shows the user's bubble. The
    // existing flow (agent.addMessage → onMessagesChanged → setState)
    // is what makes "type and hit enter" feel <16ms even if the
    // server takes a second.
    let resolveRun: (() => void) | null = null;
    fake.runAgent.mockImplementation(
      () =>
        new Promise<{ newMessages: never[] }>((resolve) => {
          resolveRun = () => resolve({ newMessages: [] });
        }),
    );
    const { result } = renderHook(() => useSkillAgent());

    let sendPromise: Promise<void>;
    act(() => {
      sendPromise = result.current.sendMessage("hello");
    });

    // Before runAgent's promise resolves, the user message must be in
    // the rendered list. This is the core perceived-snappiness
    // assertion of M2.
    await waitFor(() => {
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0]).toMatchObject({
        role: "user",
        content: "hello",
      });
    });
    expect(result.current.isLoading).toBe(true);

    // Now let runAgent finish.
    act(() => {
      resolveRun?.();
    });
    await act(async () => {
      await sendPromise!;
    });
  });

  it("clears stageLabel at the start of a new sendMessage", async () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("STAGE_PROGRESS", {
        stage: "before_model_done",
        label: "Thinking…",
        elapsed_ms: 145,
      });
    });
    await waitFor(() => expect(result.current.stageLabel).toBe("Thinking…"));

    await act(async () => {
      await result.current.sendMessage("next turn");
    });
    expect(result.current.stageLabel).toBeNull();
  });
});

describe("useSkillAgent — deterministic document attachment", () => {
  // Decision (chat-history-deep-fixes-3 / Bug F): when the user has docs
  // ticked in the workspace, the frontend passes the ids on every send
  // so the backend's before_agent_callback can load them eagerly. We do
  // NOT rely on the LLM to call retrieve_artifact / load_artifacts —
  // that path is flaky (Gemini calls it with empty args, then says
  // "you haven't provided a document"). These tests lock that contract:
  // documentIds in → forwardedProps.document_ids out, every time, with
  // no caching or short-circuiting.

  it("sendMessage forwards documentIds via forwardedProps.document_ids", async () => {
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("the claim incident one", {
        documentIds: ["doc-volunteers", "doc-claim"],
      });
    });

    expect(fake.runAgent).toHaveBeenCalledTimes(1);
    expect(fake.runAgent).toHaveBeenCalledWith({
      forwardedProps: { document_ids: ["doc-volunteers", "doc-claim"] },
    });
  });

  it("omits forwardedProps entirely when no docs and no resume flag", async () => {
    // Negative case: a fresh chat with nothing ticked must not send a
    // stray empty-list — the backend treats absence as "no docs", and
    // an empty list would still allocate state["document_ids"] = [].
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    expect(fake.runAgent).toHaveBeenCalledWith(undefined);
  });

  it("omits forwardedProps when documentIds is an empty array", async () => {
    // The chat page always passes `includedDocIds = openTabs.filter(...)`
    // even when no tabs are open, so it sends `documentIds: []`. The
    // hook must treat that the same as omitting it.
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("hello", { documentIds: [] });
    });

    expect(fake.runAgent).toHaveBeenCalledWith(undefined);
  });

  it("carries both documentIds and resumedSession when present", async () => {
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("review the doc", {
        documentIds: ["doc-claim"],
        resumedSession: true,
      });
    });

    expect(fake.runAgent).toHaveBeenCalledWith({
      forwardedProps: {
        document_ids: ["doc-claim"],
        resumed_session: true,
      },
    });
  });

  it("each sendMessage uses the documentIds passed to that call (no cross-call caching)", async () => {
    // Mirrors the screenshot scenario: turn 1 the user has no docs
    // attached; turn 2 they tick two tabs. The hook must NOT remember
    // turn 1's empty list when the chat page hands it the new array.
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("hi");
    });
    expect(fake.runAgent).toHaveBeenLastCalledWith(undefined);

    await act(async () => {
      await result.current.sendMessage("the claim incident one", {
        documentIds: ["doc-volunteers", "doc-claim"],
      });
    });
    expect(fake.runAgent).toHaveBeenLastCalledWith({
      forwardedProps: { document_ids: ["doc-volunteers", "doc-claim"] },
    });

    await act(async () => {
      await result.current.sendMessage("just one now", {
        documentIds: ["doc-claim"],
      });
    });
    expect(fake.runAgent).toHaveBeenLastCalledWith({
      forwardedProps: { document_ids: ["doc-claim"] },
    });
  });
});

describe("useSkillAgent — D2 (chat-history-deep-fixes H2): agent identity reset", () => {
  it("resets the message list when the underlying agent is replaced (F1 must yield to agent identity changes)", async () => {
    // Bug B from chat-history-deep-fixes.md: clicking "+ New conversation"
    // rebuilds AGUIProvider's HttpAgent with an empty messages array. F1's
    // monotonic guard saw next.length(0) < prev.length(N) and held the
    // old list, silently suppressing the reset.
    //
    // Correct behaviour: F1 must distinguish "agent stuttered mid-stream"
    // (shrink with same agent identity → hold) from "agent was replaced
    // entirely" (shrink with new agent identity → accept the empty list).
    //
    // We simulate AGUIProvider's useMemo rebuild by swapping currentAgent
    // before a rerender — that changes useAGUIAgent's return value, which
    // makes the hook's useEffect([agent]) re-run.

    // First mount: agent A with 3 messages.
    const agentA = new FakeAgent();
    agentA.messages = [
      { id: "m1", role: "user", content: "hello A" },
      { id: "m2", role: "assistant", content: "hi A" },
      { id: "m3", role: "user", content: "another A" },
    ];
    currentAgent = agentA;

    const { result, rerender } = renderHook(() => useSkillAgent());

    // Initial subscribe + sync runs. The hook reads agentA.messages directly.
    await waitFor(() => expect(result.current.messages).toHaveLength(3));

    // Swap to agent B (empty) and rerender — this is exactly what
    // AGUIProvider's useMemo does when sessionId changes.
    const agentB = new FakeAgent();
    agentB.messages = [];
    currentAgent = agentB;
    rerender();

    // The hook should re-subscribe to agentB and sync to its empty list.
    // Pre-fix: F1 holds the previous 3-item list because next.length(0) <
    // prev.length(3). The user keeps seeing the old conversation.
    // Post-fix: the agent-identity guard yields and the list resets.
    await waitFor(() => expect(result.current.messages).toHaveLength(0));
  });
});

describe("useSkillAgent — F1 (chat-history-fixes): monotonic message list", () => {
  it("never shrinks the message list when the agent's array transiently loses entries", async () => {
    // Real-world failure mode: AG-UI's HttpAgent state machine resets its
    // internal `messages` array on certain protocol-violation paths (e.g.
    // RUN_FINISHED arriving after RUN_ERROR). Each reset triggers
    // onMessagesChanged with a SHORTER array than the previous render.
    // Without F1's monotonic guard, React renders the shrunk list and the
    // user sees their messages disappear mid-stream.
    const { result } = renderHook(() => useSkillAgent());

    // Frame 1: three messages from a healthy stream.
    act(() => {
      fake.messages = [
        { id: "m1", role: "user", content: "hello" },
        { id: "m2", role: "assistant", content: "hi there" },
        { id: "m3", role: "user", content: "follow up" },
      ];
      fake.subscribers.forEach((s) => s.onMessagesChanged?.({}));
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(3));

    // Frame 2: the agent's state machine glitches and the array shrinks
    // to two messages. Pre-fix, useSkillAgent would mirror this — the
    // user sees their last message vanish. Post-fix, the rendered list
    // must hold at >= 3.
    act(() => {
      fake.messages = [
        { id: "m1", role: "user", content: "hello" },
        { id: "m2", role: "assistant", content: "hi there" },
      ];
      fake.subscribers.forEach((s) => s.onMessagesChanged?.({}));
    });

    // Critical assertion: the visible message count never drops below the
    // peak of the stream so far.
    expect(result.current.messages.length).toBeGreaterThanOrEqual(3);

    // Frame 3: the stream recovers and adds a new message. The rendered
    // list must accept the longer array (we're monotonic on length only,
    // not pinned to a specific snapshot).
    act(() => {
      fake.messages = [
        { id: "m1", role: "user", content: "hello" },
        { id: "m2", role: "assistant", content: "hi there" },
        { id: "m3", role: "user", content: "follow up" },
        { id: "m4", role: "assistant", content: "answering" },
      ];
      fake.subscribers.forEach((s) => s.onMessagesChanged?.({}));
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(4));
  });
});

describe("useSkillAgent — error classification", () => {
  it("sets error.kind=http status=500 retryable=true on HTTP 500 throw", async () => {
    fake.runAgent.mockRejectedValue(new Error("HTTP 500: Internal Server Error"));
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.kind).toBe("http");
    expect(result.current.error?.status).toBe(500);
    expect(result.current.error?.retryable).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });

  it("sets retryable=false on HTTP 401", async () => {
    fake.runAgent.mockRejectedValue(new Error("HTTP 401: Unauthorized"));
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.retryable).toBe(false);
    expect(result.current.error?.message).toContain("Session expired");
  });

  it("sets correct message on HTTP 502", async () => {
    fake.runAgent.mockRejectedValue(new Error("HTTP 502: Bad Gateway"));
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toContain("Can't reach the server");
    expect(result.current.error?.retryable).toBe(true);
  });

  it("sets kind=network on non-HTTP error", async () => {
    fake.runAgent.mockRejectedValue(new TypeError("fetch failed"));
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.kind).toBe("network");
    expect(result.current.error?.retryable).toBe(true);
  });

  it("sets kind=run_error when onRunFailed fires", async () => {
    // runAgent resolves (stream opened), but backend emits RUN_ERROR mid-stream
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunFailed({ message: "tool execution failed" });
      return { newMessages: [] };
    });
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.kind).toBe("run_error");
    expect(result.current.error?.retryable).toBe(true);
    expect(result.current.isLoading).toBe(false);
  });

  // Sprint 2.12 — typed budget-exceeded branch.
  it("sets kind=budget_exceeded when onRunFailed carries code=BUDGET_EXCEEDED", async () => {
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunError({
        message: "Cohort PHYS-7K2N is over its monthly budget.",
        code: "BUDGET_EXCEEDED",
        retry_after_seconds: 3600,
      });
      return { newMessages: [] };
    });
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    const err = result.current.error;
    expect(err?.kind).toBe("budget_exceeded");
    if (err?.kind === "budget_exceeded") {
      expect(err.message).toBe("Cohort PHYS-7K2N is over its monthly budget.");
      expect(err.retryAfterSeconds).toBe(3600);
      expect(err.retryable).toBe(true);
    }
  });

  it("budget_exceeded with no retry_after_seconds is non-retryable", async () => {
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunError({
        message: "Budget exceeded.",
        code: "BUDGET_EXCEEDED",
      });
      return { newMessages: [] };
    });
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    const err = result.current.error;
    expect(err?.kind).toBe("budget_exceeded");
    if (err?.kind === "budget_exceeded") {
      expect(err.retryAfterSeconds).toBeUndefined();
      expect(err.retryable).toBe(false);
    }
  });

  // Regression: budget-exceeded classifier MUST NOT capture other error
  // codes; the existing run_error branch keeps its semantics.
  it("non-BUDGET_EXCEEDED code still falls through to kind=run_error", async () => {
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunError({
        message: "Vertex auth failed",
        code: "VERTEX_AUTH_FAILED",
      });
      return { newMessages: [] };
    });
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.kind).toBe("run_error");
  });
});

describe("useSkillAgent — clearError", () => {
  it("clearError resets error to null", async () => {
    fake.runAgent.mockRejectedValue(new Error("HTTP 500: oops"));
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("test");
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    act(() => result.current.clearError());
    await waitFor(() => expect(result.current.error).toBeNull());
  });

  it("sendMessage clears existing error before running", async () => {
    // First call fails
    fake.runAgent.mockRejectedValueOnce(new Error("HTTP 500: first failure"));
    // Second call succeeds
    fake.runAgent.mockResolvedValueOnce({ newMessages: [] });

    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("first");
    });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    await act(async () => {
      await result.current.sendMessage("retry");
    });

    // Error should be cleared at the start of the second send
    await waitFor(() => expect(result.current.error).toBeNull());
  });
});

describe("useSkillAgent — hang watchdog", () => {
  // Use a very short timeout and real timers to avoid fake-timer/act interactions.
  const HANG_MS = 80;

  it("fires after timeout with no RUN_STARTED, calls abortRun, sets network error", async () => {
    // runAgent never resolves — simulates a stream that opens but sends no events.
    fake.runAgent.mockReturnValue(new Promise<{ newMessages: [] }>(() => {}));

    const { result } = renderHook(() => useSkillAgent({ _hangTimeoutMs: HANG_MS }));

    // Fire-and-forget — sendMessage blocks on runAgent.
    act(() => { void result.current.sendMessage("hang test"); });

    // Wait for the watchdog to fire (real timer, slightly past HANG_MS).
    await waitFor(() => expect(result.current.error).not.toBeNull(), { timeout: HANG_MS * 5 });

    expect(result.current.error?.kind).toBe("network");
    expect(fake.abortRun).toHaveBeenCalled();
    expect(result.current.isLoading).toBe(false);
  });

  it("does NOT fire watchdog when RUN_STARTED arrives before timeout", async () => {
    fake.runAgent.mockImplementation(async () => {
      fake.emitRunStart();
      return { newMessages: [] };
    });

    const { result } = renderHook(() => useSkillAgent({ _hangTimeoutMs: HANG_MS }));

    await act(async () => {
      await result.current.sendMessage("normal run");
    });

    // Wait longer than the timeout; watchdog must not have fired.
    await new Promise((res) => setTimeout(res, HANG_MS * 3));

    expect(result.current.error).toBeNull();
  });
});

describe("useSkillAgent — thinking/reasoning events", () => {
  it("thinkingContent starts as empty string and isThinking starts false", () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(result.current.thinkingContent).toBe("");
    expect(result.current.isThinking).toBe(false);
  });

  it("isThinking becomes true on REASONING_START", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitReasoningStart());
    expect(result.current.isThinking).toBe(true);
  });

  it("thinkingContent updates via reasoningMessageBuffer on REASONING_MESSAGE_CONTENT", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitReasoningStart());
    act(() => fake.emitReasoningContent("First chunk"));
    expect(result.current.thinkingContent).toBe("First chunk");
    act(() => fake.emitReasoningContent("First chunk Second chunk"));
    expect(result.current.thinkingContent).toBe("First chunk Second chunk");
  });

  it("isThinking becomes false on REASONING_END", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitReasoningStart());
    act(() => fake.emitReasoningEnd());
    expect(result.current.isThinking).toBe(false);
  });

  it("thinkingContent and isThinking reset on RUN_STARTED", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => fake.emitReasoningStart());
    act(() => fake.emitReasoningContent("some reasoning"));
    expect(result.current.thinkingContent).toBe("some reasoning");
    act(() => fake.emitRunStart());
    expect(result.current.thinkingContent).toBe("");
    expect(result.current.isThinking).toBe(false);
  });
});

describe("useSkillAgent — TOOL_CALL_ARGS surfacing (M3.6)", () => {
  // Locks the contract MCPAppToolCallRouter relies on to fill
  // <AppRenderer toolInput={...}>: argsJson is the concatenated
  // TOOL_CALL_ARGS deltas. The router JSON-parses on read.

  it("appends TOOL_CALL_ARGS deltas into ToolCallState.argsJson", async () => {
    const { result } = renderHook(() => useSkillAgent());

    act(() => fake.emitToolCallStart("tc-1", "show-map", "asst-1"));
    act(() => fake.emitToolCallArgs("tc-1", '{"west":'));
    act(() => fake.emitToolCallArgs("tc-1", "11.4,"));
    act(() => fake.emitToolCallArgs("tc-1", '"label":"Munich"}'));
    act(() => fake.emitToolCallEnd("tc-1"));

    await waitFor(() => {
      const tc = result.current.toolCalls.find((c) => c.id === "tc-1");
      expect(tc?.argsJson).toBe('{"west":11.4,"label":"Munich"}');
      expect(tc?.status).toBe("success");
    });
  });

  it("leaves argsJson undefined when no ARGS events fire", async () => {
    const { result } = renderHook(() => useSkillAgent());

    act(() => fake.emitToolCallStart("tc-2", "geocode", "asst-1"));
    act(() => fake.emitToolCallEnd("tc-2"));

    await waitFor(() => {
      const tc = result.current.toolCalls.find((c) => c.id === "tc-2");
      expect(tc?.argsJson).toBeUndefined();
    });
  });

  it("scopes argsJson per toolCallId (concurrent calls don't bleed)", async () => {
    const { result } = renderHook(() => useSkillAgent());

    act(() => fake.emitToolCallStart("tc-3", "show-map", "asst-1"));
    act(() => fake.emitToolCallStart("tc-4", "geocode", "asst-1"));
    act(() => fake.emitToolCallArgs("tc-3", '{"label":"A"}'));
    act(() => fake.emitToolCallArgs("tc-4", '{"query":"B"}'));

    await waitFor(() => {
      const tc3 = result.current.toolCalls.find((c) => c.id === "tc-3");
      const tc4 = result.current.toolCalls.find((c) => c.id === "tc-4");
      expect(tc3?.argsJson).toBe('{"label":"A"}');
      expect(tc4?.argsJson).toBe('{"query":"B"}');
    });
  });
});

describe("useSkillAgent — F2a: parentMessageId attribution (chat-history live-session)", () => {
  // Surfaced 2026-05-01 deployed-dev E2E: ADK doesn't emit parentMessageId
  // on TOOL_CALL_START. ADK also emits tool calls BEFORE the assistant text
  // message (tools-first pattern), so agent.messages has no string-content
  // assistant entry at TOOL_CALL_START time.
  //
  // Fix: snapshot filters to string-content messages only (tool call messages
  // have content: [...]). When no text message exists yet, parentMessageId is
  // undefined and onTextMessageStartEvent back-attributes it once the real id
  // is known. This freezes attribution before later assistant messages arrive,
  // preventing turn 1's iframes from jumping to turn 2's bubble.

  it("back-attributes tool calls to the turn's text message when tools arrive first (ADK pattern)", async () => {
    const { result } = renderHook(() => useSkillAgent());

    // Turn 1: no prior assistant text message exists. Tools fire first (geocode
    // + show-map), then the text message arrives via TEXT_MESSAGE_START.
    // Both tool calls should end up attributed to the text message id.
    act(() => fake.emitToolCallStart("tc-geocode", "geocode"));
    act(() => fake.emitToolCallStart("tc-showmap", "show-map"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-geocode")?.parentMessageId).toBeUndefined();
      expect(result.current.toolCalls.find((c) => c.id === "tc-showmap")?.parentMessageId).toBeUndefined();
    });

    // Text message arrives — both deferred tool calls must be attributed to it.
    act(() => fake.emitTextMessageStart("asst-msg-1"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-geocode")?.parentMessageId).toBe("asst-msg-1");
      expect(result.current.toolCalls.find((c) => c.id === "tc-showmap")?.parentMessageId).toBe("asst-msg-1");
    });

    // Turn 2 text message arrives — turn 1's tool calls must NOT re-attribute.
    act(() => fake.emitTextMessageStart("asst-msg-2"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-geocode")?.parentMessageId).toBe("asst-msg-1");
      expect(result.current.toolCalls.find((c) => c.id === "tc-showmap")?.parentMessageId).toBe("asst-msg-1");
    });
  });

  it("snapshots a prior turn's text message id when one exists (text-before-tool pattern)", async () => {
    const { result } = renderHook(() => useSkillAgent());

    // A prior assistant text message exists in agent.messages (string content).
    act(() => {
      fake.addMessage({ id: "user-1", role: "user", content: "show me Reykjavik" });
      fake.addMessage({ id: "asst-1", role: "assistant", content: "Here is Reykjavik." });
    });
    // Tool call for turn 2 fires after turn 1's text — should snapshot asst-1.
    act(() => fake.emitToolCallStart("tc-101", "show-map"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-101")?.parentMessageId).toBe("asst-1");
    });

    // Turn 2 text message arrives — tc-101 is already attributed, must not change.
    act(() => fake.emitTextMessageStart("asst-2"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-101")?.parentMessageId).toBe("asst-1");
    });
  });

  it("respects an explicit parentMessageId from the event (no override)", async () => {
    const { result } = renderHook(() => useSkillAgent());

    act(() => {
      fake.addMessage({ id: "asst-1", role: "assistant", content: "first turn" });
      fake.addMessage({ id: "asst-2", role: "assistant", content: "second turn" });
    });
    // Event explicitly carries asst-1 — must be preserved even though
    // asst-2 is the latest.
    act(() => fake.emitToolCallStart("tc-explicit", "geocode", "asst-1"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-explicit")?.parentMessageId).toBe("asst-1");
    });
  });

  it("leaves parentMessageId undefined at TOOL_CALL_START when no assistant text message exists yet", async () => {
    const { result } = renderHook(() => useSkillAgent());

    // Pre-first-turn: no assistant messages yet — should be undefined until
    // TEXT_MESSAGE_START arrives.
    act(() => fake.emitToolCallStart("tc-orphan", "show-map"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-orphan")?.parentMessageId).toBeUndefined();
    });
  });

  it("preserves completed tool calls across turns so prior-turn iframes survive RUN_STARTED", async () => {
    const { result } = renderHook(() => useSkillAgent());

    // ── Turn 1 ──
    act(() => fake.emitRunStart());
    act(() => fake.emitToolCallStart("tc-t1-geocode", "geocode"));
    act(() => fake.emitToolCallStart("tc-t1-showmap", "show-map"));
    // Text message arrives — back-attributes both tool calls to turn-1 message.
    act(() => fake.emitTextMessageStart("msg-turn-1"));
    act(() => fake.emitRunFinal());

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-t1-geocode")?.parentMessageId).toBe("msg-turn-1");
      expect(result.current.toolCalls.find((c) => c.id === "tc-t1-showmap")?.parentMessageId).toBe("msg-turn-1");
    });

    // ── Turn 2 ──
    // Add turn-1 text message to fake agent.messages so it's available to snapshot
    act(() => {
      fake.addMessage({ id: "msg-turn-1", role: "assistant", content: "Here is the map." });
    });
    act(() => fake.emitRunStart());

    // Turn 1 tool calls must still be in state (not wiped by RUN_STARTED).
    await waitFor(() => {
      const ids = result.current.toolCalls.map((c) => c.id);
      expect(ids).toContain("tc-t1-geocode");
      expect(ids).toContain("tc-t1-showmap");
    });

    // Turn 2 tool calls fire. The snapshot is scoped to current-run messages
    // (after RUN_STARTED), which has none yet → parentMessageId = undefined.
    act(() => fake.emitToolCallStart("tc-t2-geocode", "geocode"));
    act(() => fake.emitToolCallStart("tc-t2-showmap", "show-map"));

    await waitFor(() => {
      expect(result.current.toolCalls.find((c) => c.id === "tc-t2-geocode")?.parentMessageId).toBeUndefined();
      expect(result.current.toolCalls.find((c) => c.id === "tc-t2-showmap")?.parentMessageId).toBeUndefined();
    });

    // Turn 2 text message back-attributes only the new undefined-parent ones.
    act(() => fake.emitTextMessageStart("msg-turn-2"));
    act(() => fake.emitRunFinal());

    await waitFor(() => {
      // Turn 1 tool calls: parentMessageId still frozen at msg-turn-1.
      expect(result.current.toolCalls.find((c) => c.id === "tc-t1-geocode")?.parentMessageId).toBe("msg-turn-1");
      expect(result.current.toolCalls.find((c) => c.id === "tc-t1-showmap")?.parentMessageId).toBe("msg-turn-1");
      // Turn 2 tool calls: parentMessageId correctly set to msg-turn-2.
      expect(result.current.toolCalls.find((c) => c.id === "tc-t2-geocode")?.parentMessageId).toBe("msg-turn-2");
      expect(result.current.toolCalls.find((c) => c.id === "tc-t2-showmap")?.parentMessageId).toBe("msg-turn-2");
    });
  });
});

// Sprint 2.10 — A2UI surface snapshot ride-along on forwardedProps.
describe("useSkillAgent — A2UI surface context (sprint 2.10)", () => {
  beforeEach(() => {
    surfaceSnapshotStub = {};
  });

  it("attaches forwardedProps.a2ui_surface_state when registry has active surfaces", async () => {
    surfaceSnapshotStub = {
      workspace: {
        catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json",
        dataModel: { activeUsers: "42 users online", revenue: "$1,234" },
      },
    };
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("what's the current revenue?");
    });

    expect(fake.runAgent).toHaveBeenCalledTimes(1);
    expect(fake.runAgent).toHaveBeenCalledWith({
      forwardedProps: {
        a2ui_surface_state: {
          workspace: {
            catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json",
            dataModel: { activeUsers: "42 users online", revenue: "$1,234" },
          },
        },
      },
    });
  });

  it("merges a2ui_surface_state alongside document_ids when both present", async () => {
    surfaceSnapshotStub = {
      workspace: { catalogId: "cat", dataModel: { x: 1 } },
    };
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("hello", {
        documentIds: ["doc-1"],
      });
    });

    expect(fake.runAgent).toHaveBeenCalledWith({
      forwardedProps: {
        document_ids: ["doc-1"],
        a2ui_surface_state: {
          workspace: { catalogId: "cat", dataModel: { x: 1 } },
        },
      },
    });
  });

  it("omits a2ui_surface_state when registry snapshot is empty", async () => {
    // surfaceSnapshotStub stays {} per beforeEach.
    const { result } = renderHook(() => useSkillAgent());

    await act(async () => {
      await result.current.sendMessage("hello");
    });

    // Empty snapshot → no surface_state key → no forwardedProps at all
    // (the empty-forwardedProps short-circuit produces undefined).
    expect(fake.runAgent).toHaveBeenCalledWith(undefined);
  });

  it("reads the snapshot fresh on EACH sendMessage (no cache)", async () => {
    const { result } = renderHook(() => useSkillAgent());

    // First send: empty registry
    await act(async () => {
      await result.current.sendMessage("turn 1");
    });
    expect(fake.runAgent).toHaveBeenNthCalledWith(1, undefined);

    // Surface activates between turns
    surfaceSnapshotStub = {
      workspace: { catalogId: "cat", dataModel: { greeting: "hi" } },
    };

    // Second send: snapshot now present
    await act(async () => {
      await result.current.sendMessage("turn 2");
    });
    expect(fake.runAgent).toHaveBeenNthCalledWith(2, {
      forwardedProps: {
        a2ui_surface_state: surfaceSnapshotStub,
      },
    });
  });
});

// ---------------------------------------------------------------------------
// MODEL-RELIABILITY M1 — mid-stream inactivity watchdog.
//
// The pre-existing 30s watchdog only covers the window before RUN_STARTED.
// A stream that dies MID-run (transport cut, wedged proxy) used to hang the
// UI forever with isLoading=true. The mid-stream watchdog errors out after
// `_midStreamTimeoutMs` (default 90s) with NO traffic — where "traffic" is
// any subscriber event, crucially including the backend's no-op
// CUSTOM {name: HEARTBEAT} events emitted every 20s of model silence. A
// healthy-but-quiet stream therefore never trips it; a dead socket does.
// ---------------------------------------------------------------------------

describe("useSkillAgent — mid-stream inactivity watchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function startRun(result: { current: ReturnType<typeof useSkillAgent> }) {
    // Keep the run pending so isLoading stays true (a hung stream's shape).
    fake.runAgent.mockReturnValue(new Promise(() => {}));
    await act(async () => {
      void result.current.sendMessage("long analysis please");
    });
    act(() => {
      fake.emitRunStart();
    });
  }

  it("errors out after sustained mid-stream silence and aborts the run", async () => {
    const { result } = renderHook(() => useSkillAgent({ _midStreamTimeoutMs: 1200 }));
    await startRun(result);
    expect(result.current.isLoading).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.kind).toBe("network");
    expect(result.current.error?.retryable).toBe(true);
    expect(result.current.error?.rawMessage).toContain("stream_stall");
    expect(result.current.isLoading).toBe(false);
    expect(fake.abortRun).toHaveBeenCalled();
  });

  it("HEARTBEAT custom events reset the watchdog (quiet-but-alive stream survives)", async () => {
    const { result } = renderHook(() => useSkillAgent({ _midStreamTimeoutMs: 1200 }));
    await startRun(result);

    // Three silence windows, each under the timeout, each ended by a heartbeat.
    for (let n = 1; n <= 3; n++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(800);
      });
      act(() => {
        fake.emitCustomEvent("HEARTBEAT", { n });
      });
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    // Total elapsed ≈ 3.2s >> timeout, but no gap exceeded 1.2s → no error.
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(true);

    act(() => {
      fake.emitRunFinal();
    });
    expect(result.current.isLoading).toBe(false);
  });

  it("HEARTBEAT events are invisible: no stage label, no message churn", async () => {
    const { result } = renderHook(() => useSkillAgent({ _midStreamTimeoutMs: 1200 }));
    await startRun(result);

    const messagesBefore = result.current.messages;

    act(() => {
      fake.emitCustomEvent("HEARTBEAT", { n: 1 });
    });

    expect(result.current.stageLabel).toBeNull();
    expect(result.current.messages).toEqual(messagesBefore);
  });

  it("does not run before RUN_STARTED (that window belongs to the 30s watchdog)", async () => {
    const { result } = renderHook(() =>
      useSkillAgent({ _hangTimeoutMs: 60_000, _midStreamTimeoutMs: 1200 }),
    );
    fake.runAgent.mockReturnValue(new Promise(() => {}));
    await act(async () => {
      void result.current.sendMessage("hi");
    });
    // No RUN_STARTED yet — advance beyond the mid-stream timeout.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // Mid-stream watchdog must NOT have fired (pre-start hang is the other
    // watchdog's job, configured here at 60s so it stays quiet too).
    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// MODEL-RELIABILITY M2 — typed model-provider RUN_ERROR codes.
// Backend classifier (adk/model_errors.py) guarantees these codes; the UI
// contract is: honest copy, correct retryability, countdown for rate limits.
// ---------------------------------------------------------------------------

describe("useSkillAgent — typed model error codes (M2)", () => {
  it.each([
    ["MODEL_RATE_LIMITED", "rate_limited", true],
    ["MODEL_UNAVAILABLE", "run_error", true],
    ["MODEL_AUTH_FAILED", "run_error", false],
    ["MODEL_REQUEST_INVALID", "run_error", false],
  ] as const)("%s → kind=%s retryable=%s", (code, kind, retryable) => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitRunError({ message: "provider failed", code });
    });
    expect(result.current.error?.kind).toBe(kind);
    expect(result.current.error?.retryable).toBe(retryable);
    // Copy is human, not a raw provider string.
    expect(result.current.error?.message).not.toContain("provider failed");
  });

  it("MODEL_RATE_LIMITED carries retry_after_seconds into the countdown field", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitRunError({
        message: "rate limited",
        code: "MODEL_RATE_LIMITED",
        retry_after_seconds: 7,
      });
    });
    expect(result.current.error?.retryAfterSeconds).toBe(7);
    expect(result.current.error?.message).toContain("7s");
  });
});

// ---------------------------------------------------------------------------
// MODEL-RELIABILITY M3 — MODEL_RETRY / MODEL_FALLBACK custom events.
// Retry is transient (stage label, auto-fades); fallback is persistent
// (transcript notice) — degradation announced, never hidden.
// ---------------------------------------------------------------------------

describe("useSkillAgent — model reliability events (M3)", () => {
  it("MODEL_RETRY sets a transient stage label only", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("MODEL_RETRY", { model: "anthropic/claude-opus-4-7", attempt: 1, code: "MODEL_RATE_LIMITED" });
    });
    expect(result.current.stageLabel).toContain("retrying");
    expect(result.current.fallbacks).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("MODEL_FALLBACK appends a persistent notice with model names", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("MODEL_FALLBACK", {
        from_model: "anthropic/claude-opus-4-7",
        to_model: "gemini-3.1-pro-preview",
        code: "MODEL_UNAVAILABLE",
        provider: "anthropic",
      });
    });
    expect(result.current.fallbacks).toHaveLength(1);
    expect(result.current.fallbacks[0].fromModel).toBe("anthropic/claude-opus-4-7");
    expect(result.current.fallbacks[0].toModel).toBe("gemini-3.1-pro-preview");
    // Persistent: still present after the run finishes.
    act(() => {
      fake.emitRunFinal();
    });
    expect(result.current.fallbacks).toHaveLength(1);
  });

  it("cooldown-skip fallbacks carry the reason through", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("MODEL_FALLBACK", {
        from_model: "anthropic/claude-opus-4-7",
        to_model: "gemini-3.1-pro-preview",
        code: "MODEL_UNAVAILABLE",
        reason: "provider_cooldown",
      });
    });
    expect(result.current.fallbacks[0].reason).toBe("provider_cooldown");
  });
});

describe("useSkillAgent — AGENT_DELEGATION anchoring (transparent front-door)", () => {
  it("anchors the chip to the turn's USER message, not the front-door transfer/answer message", () => {
    const { result } = renderHook(() => useSkillAgent());
    // Reproduce the transparent front-door timeline: the user asks, the front
    // door emits a transfer_to_agent ASSISTANT message, and the specialist's
    // before_agent fires AGENT_DELEGATION *before* its answer streams into that
    // (still-open) assistant message. If we anchored to the last message of any
    // role we'd land on the assistant message → the chip drifts below the answer
    // (or into the trailing group at the very bottom).
    act(() => {
      fake.addMessage({ id: "user-1", role: "user", content: "Analyse this PPA" });
      fake.addMessage({ id: "asst-1", role: "assistant", content: "" }); // front-door transfer msg
    });
    act(() => {
      fake.emitCustomEvent("AGENT_DELEGATION", {
        parent: "one-assistant",
        target: "one-ppa-expert",
        target_display: "Contract Expert",
        mode: "auto",
      });
    });
    expect(result.current.delegations).toHaveLength(1);
    expect(result.current.delegations[0]).toMatchObject({
      afterMessageId: "user-1",
      targetDisplay: "Contract Expert",
      mode: "auto",
    });
  });

  it("falls back to a null anchor (trailing) when the turn has no user message yet", () => {
    const { result } = renderHook(() => useSkillAgent());
    act(() => {
      fake.emitCustomEvent("AGENT_DELEGATION", {
        parent: "p",
        target: "t",
        target_display: "Specialist",
        mode: "suggest",
      });
    });
    expect(result.current.delegations[0].afterMessageId).toBeNull();
  });
});
