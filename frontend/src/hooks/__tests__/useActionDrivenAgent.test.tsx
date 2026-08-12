// ACTION-TRIGGER M2 — useActionDrivenAgent tests
//
// The hook's job: POST to surface-action-run, parse SSE, dispatch A2UI
// tool-call results into SurfaceRegistry, resolve on RUN_FINISHED,
// reject on RUN_ERROR, graceful no-throw on HTTP 4xx.
//
// We mock fetchWithAuth so we control the SSE stream chunk-by-chunk,
// and mock useSurfaceRegistry so we can spy on appendMessages /
// readA2uiSurfaceState. The real registry is covered by its own tests.

import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mocks ──────────────────────────────────────────────────────────────────

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) =>
    fetchWithAuth(...(args as [RequestInfo | URL, RequestInit?])),
}));

const appendMessagesSpy = vi.fn();
const readA2uiSurfaceStateStub = vi.fn();

vi.mock("@/providers/SurfaceRegistry", async () => {
  const actual =
    await vi.importActual<typeof import("@/providers/SurfaceRegistry")>(
      "@/providers/SurfaceRegistry",
    );
  return {
    ...actual,
    useSurfaceRegistry: () => ({
      register: vi.fn(),
      unregister: vi.fn(),
      getMount: vi.fn(),
      getPolicy: vi.fn(),
      appendMessages: appendMessagesSpy,
      readA2uiSurfaceState: readA2uiSurfaceStateStub,
      clearSurface: vi.fn(),
      clearByPersistence: vi.fn(),
      getState: vi.fn(),
    }),
  };
});

// Mock AGUIProvider so the regression test below can spy on HttpAgent
// construction without instantiating a real one.
const httpAgentCtor = vi.fn();
vi.mock("@ag-ui/client", async () => {
  const actual = await vi.importActual<typeof import("@ag-ui/client")>(
    "@ag-ui/client",
  );
  class SpiedHttpAgent extends actual.HttpAgent {
    constructor(cfg: ConstructorParameters<typeof actual.HttpAgent>[0]) {
      super(cfg);
      httpAgentCtor(cfg);
    }
  }
  return { ...actual, HttpAgent: SpiedHttpAgent };
});

vi.mock("@/lib/firebase", () => ({
  subscribeToIdToken: (cb: (t: string | null) => void) => {
    queueMicrotask(() => cb("test-token"));
    return () => {};
  },
  getIdToken: async () => "test-token",
  signInWithGoogle: async () => {},
  signOut: async () => {},
}));

// ─── Imports (after mocks) ──────────────────────────────────────────────────

import { useActionDrivenAgent } from "@/hooks/useActionDrivenAgent";
import { AGUIProvider, useAGUIAgent } from "@/providers/AGUIProvider";

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Build a `Response` whose `body` is a ReadableStream yielding `chunks`
 * as `text/event-stream` frames. Each chunk is a complete `data: ...\n\n`
 * SSE frame (string) — passed through verbatim.
 */
function sseResponse(chunks: string[], init: ResponseInit = {}): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
    ...init,
  });
}

/** Encode an AG-UI event JSON object as one SSE frame. */
function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n\n`;
}

// ─── beforeEach ─────────────────────────────────────────────────────────────

beforeEach(() => {
  fetchWithAuth.mockReset();
  appendMessagesSpy.mockReset();
  readA2uiSurfaceStateStub.mockReset();
  readA2uiSurfaceStateStub.mockReturnValue({});
  httpAgentCtor.mockReset();
});

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("useActionDrivenAgent", () => {
  it("POSTs to the correct URL with {surfaceId, action, forwardedProps.a2ui_surface_state} payload", async () => {
    const snapshot = {
      workspace: { catalogId: "basic", dataModel: { counter: 0 } },
    };
    readA2uiSurfaceStateStub.mockReturnValue(snapshot);
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED", threadId: "s-1", runId: "r-1" }),
        frame({ type: "RUN_FINISHED", threadId: "s-1", runId: "r-1" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await result.current.triggerAction("workspace", {
      name: "increment",
      sourceComponentId: "btn-1",
      context: { foo: "bar" },
    });

    expect(fetchWithAuth).toHaveBeenCalledOnce();
    const [url, init] = fetchWithAuth.mock.calls[0];
    expect(url).toBe(
      "/api/proxy/api/skills/skill-x/sessions/sess-1/surface-action-run",
    );
    expect(init?.method).toBe("POST");
    expect(
      (init?.headers as Record<string, string>)["Content-Type"],
    ).toBe("application/json");
    const body = JSON.parse(init?.body as string);
    expect(body).toEqual({
      surfaceId: "workspace",
      action: {
        name: "increment",
        sourceComponentId: "btn-1",
        context: { foo: "bar" },
      },
      forwardedProps: { a2ui_surface_state: snapshot },
    });
  });

  it("URL-encodes skillId and sessionId so weird ids don't break routing", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([frame({ type: "RUN_FINISHED" })]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({
        skillId: "weird/skill id",
        sessionId: "sess with space",
      }),
    );
    await result.current.triggerAction("workspace", { name: "click" });

    const [url] = fetchWithAuth.mock.calls[0];
    expect(url).toBe(
      "/api/proxy/api/skills/weird%2Fskill%20id/sessions/sess%20with%20space/surface-action-run",
    );
  });

  it("happy path: parses SSE, dispatches send_a2ui_json_to_client tool-call results into SurfaceRegistry, resolves on RUN_FINISHED", async () => {
    const a2uiMessages = [
      {
        version: "v0.9",
        createSurface: { surfaceId: "workspace", catalogId: "basic" },
      },
      {
        version: "v0.9",
        updateComponents: { surfaceId: "workspace", components: [] },
      },
    ];
    const envelope = JSON.stringify({
      validated_a2ui_json: a2uiMessages,
      surface_id: "workspace",
    });

    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED", threadId: "s", runId: "r" }),
        frame({
          type: "TOOL_CALL_START",
          toolCallId: "tc-1",
          toolCallName: "send_a2ui_json_to_client",
        }),
        frame({ type: "TOOL_CALL_ARGS", toolCallId: "tc-1", delta: "{}" }),
        frame({ type: "TOOL_CALL_END", toolCallId: "tc-1" }),
        frame({
          type: "TOOL_CALL_RESULT",
          messageId: "m-1",
          toolCallId: "tc-1",
          content: envelope,
        }),
        frame({ type: "RUN_FINISHED", threadId: "s", runId: "r" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "increment" }),
    ).resolves.toBeUndefined();

    expect(appendMessagesSpy).toHaveBeenCalledOnce();
    expect(appendMessagesSpy).toHaveBeenCalledWith(
      "workspace",
      a2uiMessages,
      "tc-1",
    );
  });

  it("Model-B path: dispatches an out-of-model A2UI_SURFACE CUSTOM event into the SurfaceRegistry (the 'launcher does nothing' regression)", async () => {
    // one-doc-compare / one-ppa-expert (a2ui.enabled: false) never call
    // send_a2ui_json_to_client — their surface arrives as a CUSTOM event from
    // the backend result→A2UI emitter. Before the fix this hook ignored CUSTOM
    // events, so a launcher-triggered compare/analyze rendered nothing.
    const a2uiMessages = [
      { version: "v0.9", createSurface: { surfaceId: "ppa_comparison", catalogId: "basic" } },
      { version: "v0.9", updateComponents: { surfaceId: "ppa_comparison", components: [] } },
    ];
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED", threadId: "s", runId: "r" }),
        frame({
          type: "CUSTOM",
          name: "A2UI_SURFACE",
          value: {
            surfaceId: "ppa_comparison",
            messages: a2uiMessages,
            sourceId: "src-abc",
            artifact: { kind: "ppa_comparison", title: "A vs B" },
          },
        }),
        frame({ type: "RUN_FINISHED", threadId: "s", runId: "r" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "one-doc-compare", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "start_compare" }),
    ).resolves.toBeUndefined();

    expect(appendMessagesSpy).toHaveBeenCalledOnce();
    expect(appendMessagesSpy).toHaveBeenCalledWith(
      "ppa_comparison",
      a2uiMessages,
      "src-abc",
      { kind: "ppa_comparison", title: "A vs B" },
    );
  });

  it("skips non-A2UI tool call results — only send_a2ui_json_to_client dispatches into the registry", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({
          type: "TOOL_CALL_START",
          toolCallId: "tc-search",
          toolCallName: "search_documents",
        }),
        frame({
          type: "TOOL_CALL_RESULT",
          messageId: "m-1",
          toolCallId: "tc-search",
          content: JSON.stringify({ hits: [] }),
        }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );
    await result.current.triggerAction("workspace", { name: "click" });
    expect(appendMessagesSpy).not.toHaveBeenCalled();
  });

  it("NEVER SILENT: a 403 rejects with a visible message (opt-in is decided before triggerAction)", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchWithAuth.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "not opted in" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    // Throws so the caller (A2UISurfaceMount / CompareLauncher) can SHOW it —
    // never a silent grey surface.
    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).rejects.toThrow(/not permitted|403/i);

    expect(appendMessagesSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("rejects on RUN_ERROR with the server-provided message", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({ type: "RUN_ERROR", message: "tool blew up" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).rejects.toThrow(/tool blew up/);
  });

  it("G41 dedup: RUN_ERROR followed by RUN_FINISHED still rejects exactly once and does not double-resolve", async () => {
    // Server's G41 dedup wrapper should never emit both, but the hook
    // must defend against the variant where a duplicate slips through —
    // first terminal wins, second is ignored.
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({ type: "RUN_ERROR", message: "first" }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).rejects.toThrow(/first/);
  });

  it("G41 dedup: RUN_FINISHED followed by RUN_ERROR resolves cleanly (first terminal wins)", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({ type: "RUN_FINISHED" }),
        frame({ type: "RUN_ERROR", message: "should be ignored" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).resolves.toBeUndefined();
  });

  it("NEVER SILENT: a network error rejects with a visible message (not a silent resolve)", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchWithAuth.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );

    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).rejects.toThrow(/couldn't reach the server/i);
    expect(appendMessagesSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it("omits a2ui_surface_state-fallback: empty snapshot still serializes (forwardedProps.a2ui_surface_state = {})", async () => {
    readA2uiSurfaceStateStub.mockReturnValue({});
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([frame({ type: "RUN_FINISHED" })]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );
    await result.current.triggerAction("workspace", { name: "click" });

    const body = JSON.parse(fetchWithAuth.mock.calls[0][1].body);
    expect(body.forwardedProps).toEqual({ a2ui_surface_state: {} });
  });

  // ─── ACTIVITY-OBS — the activitySink live feed ────────────────────────────

  function makeSink() {
    return {
      upsertToolCall: vi.fn(),
      upsertDelegation: vi.fn(),
      onRunStart: vi.fn(),
      onRunSettled: vi.fn(),
      onStage: vi.fn(),
    };
  }

  it("activitySink: drives tool-call transitions (running → success), fires onRunStart + onRunSettled(no error) on RUN_FINISHED", async () => {
    const sink = makeSink();
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED", threadId: "s", runId: "r" }),
        frame({
          type: "TOOL_CALL_START",
          toolCallId: "tc-extract",
          toolCallName: "extract_ppa_clauses",
        }),
        frame({ type: "TOOL_CALL_ARGS", toolCallId: "tc-extract", delta: '{"doc":' }),
        frame({ type: "TOOL_CALL_ARGS", toolCallId: "tc-extract", delta: '"a"}' }),
        frame({ type: "TOOL_CALL_END", toolCallId: "tc-extract" }),
        frame({
          type: "TOOL_CALL_RESULT",
          toolCallId: "tc-extract",
          content: JSON.stringify({ clauses: [] }),
        }),
        frame({ type: "RUN_FINISHED", threadId: "s", runId: "r" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({
        skillId: "one-doc-compare",
        sessionId: "sess-1",
        activitySink: sink,
      }),
    );
    await result.current.triggerAction("workspace", { name: "start_compare" });

    expect(sink.onRunStart).toHaveBeenCalledOnce();
    // First upsert marks the tool call running…
    const firstCall = sink.upsertToolCall.mock.calls[0][0];
    expect(firstCall).toMatchObject({
      id: "tc-extract",
      name: "extract_ppa_clauses",
      status: "running",
    });
    expect(typeof firstCall.ts).toBe("number");
    // …and the LAST upsert for that id lands on success with the result content.
    const lastForId = [...sink.upsertToolCall.mock.calls]
      .map((c) => c[0])
      .filter((tc) => tc.id === "tc-extract")
      .at(-1);
    expect(lastForId).toMatchObject({ status: "success" });
    expect(lastForId.argsJson).toBe('{"doc":"a"}');
    expect(lastForId.resultContent).toContain("clauses");
    // Settled exactly once, with no error → ChatShell re-syncs /activity.
    expect(sink.onRunSettled).toHaveBeenCalledOnce();
    expect(sink.onRunSettled).toHaveBeenCalledWith({});
  });

  it("activitySink: RUN_ERROR flips a running tool call to error and settles with the message", async () => {
    const sink = makeSink();
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({
          type: "TOOL_CALL_START",
          toolCallId: "tc-map",
          toolCallName: "map_ppa_obligations",
        }),
        frame({ type: "RUN_ERROR", message: "obligation mapper blew up" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({
        skillId: "one-ppa-expert",
        sessionId: "sess-1",
        activitySink: sink,
      }),
    );
    await expect(
      result.current.triggerAction("workspace", { name: "start_obligation_analysis" }),
    ).rejects.toThrow(/obligation mapper blew up/);

    const lastForId = [...sink.upsertToolCall.mock.calls]
      .map((c) => c[0])
      .filter((tc) => tc.id === "tc-map")
      .at(-1);
    expect(lastForId).toMatchObject({ status: "error" });
    expect(sink.onRunSettled).toHaveBeenCalledOnce();
    expect(sink.onRunSettled).toHaveBeenCalledWith({ error: "obligation mapper blew up" });
  });

  it("activitySink: forwards STAGE_PROGRESS labels and AGENT_DELEGATION markers", async () => {
    const sink = makeSink();
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({ type: "CUSTOM", name: "STAGE_PROGRESS", value: { label: "Reading 2 documents…" } }),
        frame({
          type: "CUSTOM",
          name: "AGENT_DELEGATION",
          value: { parent: "one-ppa-expert", target: "clause-extractor", target_display: "Clause Extractor", mode: "auto" },
        }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    );

    const { result } = renderHook(() =>
      useActionDrivenAgent({
        skillId: "one-ppa-expert",
        sessionId: "sess-1",
        activitySink: sink,
      }),
    );
    await result.current.triggerAction("workspace", { name: "start_obligation_analysis" });

    expect(sink.onStage).toHaveBeenCalledWith("Reading 2 documents…");
    expect(sink.upsertDelegation).toHaveBeenCalledOnce();
    expect(sink.upsertDelegation.mock.calls[0][0]).toMatchObject({
      target: "clause-extractor",
      targetDisplay: "Clause Extractor",
      mode: "auto",
    });
  });

  it("activitySink: settles even when the stream ends without a terminal event", async () => {
    const sink = makeSink();
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([frame({ type: "RUN_STARTED" })]),
    );
    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "s", sessionId: "sess-1", activitySink: sink }),
    );
    await result.current.triggerAction("workspace", { name: "click" });
    expect(sink.onRunStart).toHaveBeenCalledOnce();
    expect(sink.onRunSettled).toHaveBeenCalledOnce();
    expect(sink.onRunSettled).toHaveBeenCalledWith({});
  });

  it("no activitySink: hook still works (backward compatible)", async () => {
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "TOOL_CALL_START", toolCallId: "t", toolCallName: "x" }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    );
    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "s", sessionId: "sess-1" }),
    );
    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).resolves.toBeUndefined();
  });

  it("handles SSE frames split across read() chunks (buffer reassembly)", async () => {
    // Simulate a server flushing a frame in two write() calls. The
    // reader's `\n\n` split must reassemble across the chunk boundary.
    const part1 = `data: ${JSON.stringify({ type: "RUN_STARTED" })}\n`;
    const part2 = `\ndata: ${JSON.stringify({ type: "RUN_FINISHED" })}\n\n`;

    fetchWithAuth.mockResolvedValueOnce(sseResponse([part1, part2]));

    const { result } = renderHook(() =>
      useActionDrivenAgent({ skillId: "skill-x", sessionId: "sess-1" }),
    );
    await expect(
      result.current.triggerAction("workspace", { name: "click" }),
    ).resolves.toBeUndefined();
  });
});

// ─── M2.3 regression — HttpAgent stability across action-triggered runs ──

describe("useActionDrivenAgent — D1-style regression (chat-history-deep-fixes H1)", () => {
  it("does NOT rebuild the AGUIProvider's HttpAgent when an action-triggered run completes (same session_id throughout)", async () => {
    // Pattern mirrors providers/__tests__/AGUIProvider.test.tsx:111
    // ("rebuilds the HttpAgent when sessionId changes from undefined to a
    // server-assigned value"). The contract here is the inverse: if the
    // sessionId stays the same across a `triggerAction()` call, no new
    // HttpAgent is constructed — the action-triggered run shares the
    // same backing session and must NOT cause the chat path's agent to
    // be torn down (which would discard in-flight chat state).
    fetchWithAuth.mockResolvedValueOnce(
      sseResponse([
        frame({ type: "RUN_STARTED" }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    );

    function Harness() {
      // Capture the agent so we know the AGUIProvider mounted. The agent
      // identity itself isn't load-bearing for this test — we just need
      // a real provider in the tree to count HttpAgent constructions.
      useAGUIAgent();
      return null;
    }

    const { result } = renderHook(
      () =>
        useActionDrivenAgent({
          skillId: "skill-x",
          sessionId: "sess-stable-1",
        }),
      {
        wrapper: ({ children }) => (
          <AGUIProvider skillId="skill-x" sessionId="sess-stable-1">
            <Harness />
            {children}
          </AGUIProvider>
        ),
      },
    );

    await waitFor(() => {
      expect(httpAgentCtor.mock.calls.length).toBeGreaterThanOrEqual(1);
    });
    const ctorCallsBefore = httpAgentCtor.mock.calls.length;

    await result.current.triggerAction("workspace", { name: "click" });

    // Contract: the HttpAgent is owned by AGUIProvider and is keyed by
    // (skillId, sessionId). useActionDrivenAgent does not touch the
    // AGUIProvider's HttpAgent at all — it builds its own fetch+SSE
    // pipeline. So no extra constructor call should fire.
    expect(httpAgentCtor.mock.calls.length).toBe(ctorCallsBefore);
  });
});

