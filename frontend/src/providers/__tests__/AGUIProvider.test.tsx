import { render, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { HttpAgent } from "@ag-ui/client";
import { AuthProvider } from "@/contexts/AuthContext";
import { AGUIProvider, useAGUIAgent } from "@/providers/AGUIProvider";

// Mock-controlled subscriber: tests trigger token rotations via `tokenSubscribers`.
const tokenSubscribers = new Set<(token: string | null) => void>();
function fireTokenRotation(token: string | null) {
  tokenSubscribers.forEach((cb) => cb(token));
}

vi.mock("@/lib/firebase", () => ({
  subscribeToAuthState: (cb: (u: null) => void) => {
    queueMicrotask(() => cb(null));
    return () => {};
  },
  subscribeToIdToken: (cb: (token: string | null) => void) => {
    tokenSubscribers.add(cb);
    // Fire immediately with the current token (matches Firebase
    // onIdTokenChanged behaviour: synchronous-ish first delivery).
    queueMicrotask(() => cb("test-token"));
    return () => {
      tokenSubscribers.delete(cb);
    };
  },
  getIdToken: async () => "test-token",
  signInWithGoogle: async () => {},
  signInWithGoogleRedirect: async () => {},
  signOut: async () => {},
}));

// Spy on HttpAgent construction without replacing the class — consumers still
// get a real AbstractAgent subclass, which matters for useAGUIAgent().
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

describe("AGUIProvider", () => {
  it("renders children, builds an HttpAgent at the skill stream endpoint, and mutates the auth header in place from subscribeToIdToken (G40)", async () => {
    let capturedAgent: HttpAgent | null = null;
    function AgentCapture() {
      capturedAgent = useAGUIAgent();
      return <div>chat-content</div>;
    }

    const { getByText } = render(
      <AuthProvider>
        <AGUIProvider skillId="my-skill">
          <AgentCapture />
        </AGUIProvider>
      </AuthProvider>,
    );

    expect(getByText("chat-content")).toBeTruthy();

    // G40: the agent is constructed with EMPTY headers — the auth
    // header arrives later via subscribeToIdToken's first callback.
    const ctorCfg = httpAgentCtor.mock.calls[0]?.[0];
    expect(ctorCfg?.url).toBe("/api/proxy/api/skill/my-skill/stream");
    expect(ctorCfg?.headers).toEqual({});

    // After subscribeToIdToken's first delivery, the live agent
    // instance's headers now carry the bearer token. Pin both
    // (a) no extra ctor call (agent is stable across token landings)
    // and (b) the same agent instance the context exposes.
    await waitFor(() => {
      expect(httpAgentCtor.mock.calls.length).toBe(1);
      expect(capturedAgent).not.toBeNull();
      expect((capturedAgent!.headers as Record<string, string>).Authorization).toBe(
        "Bearer test-token",
      );
    });
  });

  it("URL-encodes skillId so slashes/spaces don't break the endpoint", async () => {
    render(
      <AuthProvider>
        <AGUIProvider skillId="weird id/with slash">
          <div />
        </AGUIProvider>
      </AuthProvider>,
    );

    await waitFor(() => {
      const lastCfg = httpAgentCtor.mock.calls.at(-1)?.[0];
      expect(lastCfg?.url).toBe(
        "/api/proxy/api/skill/weird%20id%2Fwith%20slash/stream",
      );
    });
  });

  it("useAGUIAgent throws outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAGUIAgent())).toThrow(
      /must be used within an AGUIProvider/,
    );
    spy.mockRestore();
  });

  it("D1 (chat-history-deep-fixes H1): rebuilds the HttpAgent when sessionId changes from undefined to a server-assigned value", async () => {
    // The real-world failure: a fresh chat starts with sessionId=undefined.
    // After the first turn the chat page's URL-writeback effect rewrites
    // ?session=<id>, which propagates to <AGUIProvider sessionId={id}>.
    // useMemo([skillId, token, sessionId]) sees the dep change and builds
    // a NEW HttpAgent instance — the old one (with [Q1, A1] in messages)
    // is discarded. This test asserts the rebuild *does* happen so we
    // know what mechanism the fix needs to neutralise (pre-allocate
    // threadId at mount so it never changes from undefined to a value).
    httpAgentCtor.mockClear();

    const { rerender } = render(
      <AuthProvider>
        <AGUIProvider skillId="skill-x" sessionId={undefined}>
          <div />
        </AGUIProvider>
      </AuthProvider>,
    );

    await waitFor(() => {
      // Initial constructor call(s) — there can be 1 (no token yet) + 1
      // (with token) depending on auth bootstrap timing. Snapshot that count.
      expect(httpAgentCtor.mock.calls.length).toBeGreaterThanOrEqual(1);
    });
    const callsBeforeWriteback = httpAgentCtor.mock.calls.length;

    // Simulate the URL-writeback: sessionId changes from undefined to <id>.
    rerender(
      <AuthProvider>
        <AGUIProvider skillId="skill-x" sessionId="server-assigned-id-123">
          <div />
        </AGUIProvider>
      </AuthProvider>,
    );

    // Pre-fix: a NEW HttpAgent is constructed because useMemo's deps
    // include sessionId. Post-fix (option a from the design doc): the
    // threadId is pre-allocated at chat-page mount, so AGUIProvider never
    // sees this transition — the constructor count stays equal.
    await waitFor(() => {
      expect(httpAgentCtor.mock.calls.length).toBeGreaterThan(callsBeforeWriteback);
    });

    // Confirm the new instance got the new threadId
    const newestCfg = httpAgentCtor.mock.calls.at(-1)?.[0];
    expect(newestCfg?.threadId).toBe("server-assigned-id-123");
  });

  it("G38 (template-chat-surface-defaults.md): children stay mounted across re-renders — no token-refresh unmount gate", async () => {
    // Critical contract: AGUIProvider must NEVER unmount its children
    // mid-conversation. A future "defensive" refactor that adds a
    // render gate (`if (!tokenResolved) return null`) would reintroduce
    // the AIPLA flicker bug (mid-chat "Earlier in this conversation"
    // history disappearing for ~400ms per hourly Firebase token refresh
    // on cross-region deploys). This test pins the contract.
    //
    // Test strategy: render a child with a stable ref. Re-render the
    // provider multiple times (simulating onAuthStateChanged churn).
    // If the child gets unmounted-and-remounted, the ref will be reset
    // to null at the moment of unmount. If hidden-className-only or
    // never-blanked, the ref stays attached to the same DOM node.
    let childRefAtMount: HTMLElement | null = null;
    let childRefAfterRerenders: HTMLElement | null = null;

    function ChildWithRef() {
      return (
        <div
          ref={(el) => {
            if (el && !childRefAtMount) childRefAtMount = el;
            childRefAfterRerenders = el;
          }}
          data-testid="agui-child"
        >
          chat-content
        </div>
      );
    }

    const { rerender, getByTestId } = render(
      <AuthProvider>
        <AGUIProvider skillId="skill-x">
          <ChildWithRef />
        </AGUIProvider>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(getByTestId("agui-child")).toBeInTheDocument();
    });
    const firstRef = childRefAtMount;
    expect(firstRef).not.toBeNull();

    // Re-render several times (the kind of churn a token refresh would
    // cause if we had a gate). The child must remain mounted throughout.
    for (let i = 0; i < 3; i++) {
      rerender(
        <AuthProvider>
          <AGUIProvider skillId="skill-x">
            <ChildWithRef />
          </AGUIProvider>
        </AuthProvider>,
      );
    }

    // Same DOM node — child was never unmounted.
    expect(childRefAfterRerenders).toBe(firstRef);
    expect(getByTestId("agui-child")).toBeInTheDocument();
  });

  it("G40 (template-auth-token-refresh.md): token rotation mutates agent.headers.Authorization in place without rebuilding the agent", async () => {
    // Real-world failure: after ~1h, Firebase rotates the ID token
    // silently. Pre-G40 the AGUIProvider had baked the original token
    // into the HttpAgent headers at construction time, so every
    // subsequent runAgent() call carried the stale (now-expired) token
    // and got 401. The fix mutates agent.headers in place when
    // subscribeToIdToken delivers a new token — no agent rebuild, no
    // unmount, no extra ctor call. This test pins the contract.
    httpAgentCtor.mockClear();

    let capturedAgent: HttpAgent | null = null;
    function AgentCapture() {
      capturedAgent = useAGUIAgent();
      return <div data-testid="agui-child">chat-content</div>;
    }

    render(
      <AuthProvider>
        <AGUIProvider skillId="long-running-skill">
          <AgentCapture />
        </AGUIProvider>
      </AuthProvider>,
    );

    // Wait for the first token to land (subscribeToIdToken's microtask).
    await waitFor(() => {
      expect(capturedAgent).not.toBeNull();
      expect((capturedAgent!.headers as Record<string, string>).Authorization).toBe(
        "Bearer test-token",
      );
    });
    expect(httpAgentCtor.mock.calls.length).toBe(1);
    const agentBeforeRotation = capturedAgent;

    // Simulate Firebase's silent ~hourly token rotation.
    fireTokenRotation("Bearer-NEW-rotated-token");

    // Contract:
    //  (a) header was swapped in place to reflect the new token
    //  (b) the AGENT INSTANCE is the same — no rebuild
    //  (c) no extra HttpAgent constructor invocation
    await waitFor(() => {
      expect((capturedAgent!.headers as Record<string, string>).Authorization).toBe(
        "Bearer Bearer-NEW-rotated-token",
      );
    });
    expect(capturedAgent).toBe(agentBeforeRotation);
    expect(httpAgentCtor.mock.calls.length).toBe(1); // no rebuild
  });

  it("G40: sign-out (token=null) strips the Authorization header without rebuilding the agent", async () => {
    httpAgentCtor.mockClear();
    let capturedAgent: HttpAgent | null = null;
    function AgentCapture() {
      capturedAgent = useAGUIAgent();
      return <div />;
    }

    render(
      <AuthProvider>
        <AGUIProvider skillId="skill-x">
          <AgentCapture />
        </AGUIProvider>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(capturedAgent).not.toBeNull();
      expect((capturedAgent!.headers as Record<string, string>).Authorization).toBe(
        "Bearer test-token",
      );
    });

    // Simulate sign-out: subscribeToIdToken delivers null.
    fireTokenRotation(null);

    await waitFor(() => {
      // Authorization header gone, but the agent instance is the same.
      expect((capturedAgent!.headers as Record<string, string>).Authorization).toBeUndefined();
    });
    expect(httpAgentCtor.mock.calls.length).toBe(1); // still no rebuild
  });
});
