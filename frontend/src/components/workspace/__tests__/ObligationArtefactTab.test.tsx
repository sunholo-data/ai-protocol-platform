// PPA-OBLIGATION 7.6 M3 — ObligationArtefactTab tests
//
// The tab mounts the M1 Obligation Analysis MCP-App artefact and boots it with
// the REAL extracted payload the map_ppa_obligations transform injected into
// the surface data model. Verifies payload injection, late-payload delivery,
// and scenario save/restore across a re-mount (tab switch), all with a mocked
// StaticArtefactFrame so we can drive its host callbacks directly.

import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mocks (must precede the import under test) ──────────────────────────────

const sendNotificationSpy = vi.fn();
interface FrameProps {
  onInitialized?: () => void;
  onUpdateModelContext: (c: Record<string, unknown>) => void;
  sandboxOrigin: string;
  artefactPath: string;
  hostContext?: { theme?: string };
}
let capturedProps: FrameProps | null = null;

vi.mock("@/components/workspace/StaticArtefactFrame", () => ({
  StaticArtefactFrame: React.forwardRef(function MockFrame(props: FrameProps, ref: React.Ref<unknown>) {
    capturedProps = props;
    React.useImperativeHandle(ref, () => ({ sendNotification: sendNotificationSpy }), []);
    return <div data-testid="mock-artefact-frame" />;
  }),
}));

const appendMessagesSpy = vi.fn();
// Mutable surface-data-model root the mocked useSurfaceState reads.
let surfaceRoot: { payload?: unknown; scenario?: unknown; reviewed?: unknown } | null = null;

vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => ({
    surface: { dataModel: { get: (p: string) => (p === "/" ? surfaceRoot : undefined) } },
  }),
  useSurfaceRegistry: () => ({ appendMessages: appendMessagesSpy }),
}));

const fetchWithAuthSpy = vi.fn(() => Promise.resolve({ ok: true } as Response));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuthSpy(...(args as [])),
}));

// ─── Import under test (after mocks) ─────────────────────────────────────────

import {
  ObligationArtefactTab,
  __resetObligationScenarioStore,
} from "../ObligationArtefactTab";

const PAYLOAD = {
  doc_id: "doc-xyz",
  effectiveDate: "2024-01-01",
  obligations: [{ id: "COD", deadline: 731, price: 0 }],
  events: [],
  policy: { penPerDay: 500, penCap: 25000, payWithin: 30, cureDays: 30, ratePct: 1, ratePeriod: 30 },
};

beforeEach(() => {
  __resetObligationScenarioStore();
  sendNotificationSpy.mockClear();
  appendMessagesSpy.mockClear();
  fetchWithAuthSpy.mockClear();
  capturedProps = null;
  surfaceRoot = { payload: PAYLOAD };
});

afterEach(() => {
  vi.clearAllMocks();
});

function lastNotification() {
  return sendNotificationSpy.mock.calls[sendNotificationSpy.mock.calls.length - 1];
}

describe("ObligationArtefactTab", () => {
  it("shows a preparing placeholder until the payload data model resolves", () => {
    surfaceRoot = null;
    render(<ObligationArtefactTab surfaceId="obligation_analysis:doc-xyz" />);
    expect(screen.getByTestId("obligation-artefact-preparing")).toBeInTheDocument();
    expect(screen.queryByTestId("mock-artefact-frame")).not.toBeInTheDocument();
  });

  it("mounts the artefact at the right path/origin and injects the payload on init", () => {
    render(<ObligationArtefactTab surfaceId="obligation_analysis:doc-xyz" />);
    expect(screen.getByTestId("mock-artefact-frame")).toBeInTheDocument();
    expect(capturedProps?.artefactPath).toBe("ppa-obligation-analysis/v1");
    expect(capturedProps?.sandboxOrigin).toMatch(/^https?:\/\//);

    act(() => capturedProps?.onInitialized?.());

    expect(sendNotificationSpy).toHaveBeenCalledTimes(1);
    const [method, params] = lastNotification();
    expect(method).toBe("ui/obligation/payload");
    expect(params.payload).toEqual(PAYLOAD);
    expect(params.savedScenario).toBeUndefined();
  });

  it("does NOT push the payload before the init handshake completes", () => {
    render(<ObligationArtefactTab surfaceId="s1" />);
    // Frame mounted with a payload, but no onInitialized yet → no notification
    // (an early postMessage would race the proxy bring-up).
    expect(sendNotificationSpy).not.toHaveBeenCalled();
  });

  it("resends when a NEW payload arrives on an already-initialised surface", () => {
    const { rerender } = render(<ObligationArtefactTab surfaceId="s1b" />);
    act(() => capturedProps?.onInitialized?.());
    expect(sendNotificationSpy).toHaveBeenCalledTimes(1);

    const nextPayload = { ...PAYLOAD, obligations: [{ id: "COD", deadline: 900, price: 0 }] };
    surfaceRoot = { payload: nextPayload };
    act(() => rerender(<ObligationArtefactTab surfaceId="s1b" />));

    expect(sendNotificationSpy).toHaveBeenCalledTimes(2);
    expect(lastNotification()[1].payload).toEqual(nextPayload);
  });

  it("persists a what-if scenario and restores it as savedScenario on the next mount", () => {
    const { unmount } = render(<ObligationArtefactTab surfaceId="s2" />);
    act(() => capturedProps?.onInitialized?.());
    sendNotificationSpy.mockClear();

    const scenario = { deadlineDelta: { COD: 21 }, waive: { "COD-payment": true } };
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario }));

    // Mirrored into the 7.5 artefact-state slot (merged with the payload).
    expect(appendMessagesSpy).toHaveBeenCalled();
    const [, messages] = appendMessagesSpy.mock.calls[0];
    expect(messages[0].updateDataModel.value).toEqual({ payload: PAYLOAD, scenario });

    // Re-mount (tab switch) → the stored scenario is injected back.
    unmount();
    sendNotificationSpy.mockClear();
    render(<ObligationArtefactTab surfaceId="s2" />);
    act(() => capturedProps?.onInitialized?.());
    const [, params] = lastNotification();
    expect(params.savedScenario).toEqual(scenario);
  });

  it("reset clears the stored scenario so the next mount boots the canonical payload", () => {
    render(<ObligationArtefactTab surfaceId="s3" />);
    act(() => capturedProps?.onInitialized?.());
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: { fmLenDelta: 10 } }));
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.reset" }));

    // The reset write drops the scenario (value carries only the payload).
    const resetCall = appendMessagesSpy.mock.calls.at(-1);
    expect(resetCall?.[1][0].updateDataModel.value).toEqual({ payload: PAYLOAD });

    sendNotificationSpy.mockClear();
    render(<ObligationArtefactTab surfaceId="s3" />);
    act(() => capturedProps?.onInitialized?.());
    const [, params] = lastNotification();
    expect(params.savedScenario).toBeUndefined();
  });

  // ── on-screen `view` → agent (7.9) ────────────────────────────────────────

  it("mirrors the artefact `view` (on-screen result) into the agent-visible surface state", () => {
    render(<ObligationArtefactTab surfaceId="v1" />);
    act(() => capturedProps?.onInitialized?.());

    const view = {
      netSettlement: "Vendor pays Client €125,000",
      vendorOwes: 125000,
      clientOwes: 0,
      terminated: false,
      obligations: [{ id: "COD", delivery: "DELIVERED", deliveryLateDays: 18, penalty: 125000 }],
      whatIfModified: false,
      reviewedOverridesActive: false,
    };
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: {}, view }));

    // The mirror carries `view` so readA2uiSurfaceState → a2ui_surface_state
    // feeds the agent the on-screen RESULT (net/penalties), not just the inputs.
    const [, messages] = appendMessagesSpy.mock.calls.at(-1)!;
    expect(messages[0].updateDataModel.value).toEqual({ payload: PAYLOAD, scenario: {}, view });
  });

  it("keeps the last on-screen `view` across a what-if reset (agent copy not dropped)", () => {
    render(<ObligationArtefactTab surfaceId="v2" />);
    act(() => capturedProps?.onInitialized?.());

    const view = { netSettlement: "Settled — no net payment due", vendorOwes: 0, clientOwes: 0, terminated: false };
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: { fmLenDelta: 5 }, view }));
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.reset" }));

    // Scenario dropped, but the last on-screen result is preserved for the agent.
    const resetCall = appendMessagesSpy.mock.calls.at(-1);
    expect(resetCall?.[1][0].updateDataModel.value).toEqual({ payload: PAYLOAD, view });
  });

  // ── reviewed-settings overlay (design open question 2) ────────────────────

  it("persists a reviewed overlay and restores it as reviewedSettings on the next mount", () => {
    const { unmount } = render(<ObligationArtefactTab surfaceId="rv1" />);
    act(() => capturedProps?.onInitialized?.());
    sendNotificationSpy.mockClear();

    const reviewed = { policy: { penPerDay: 5000 }, obligation_prices: { COD: 5000000 } };
    act(() =>
      capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: {}, reviewed }),
    );

    // Mirrored into the artefact-state slot as a distinct data-model dimension.
    const [, messages] = appendMessagesSpy.mock.calls.at(-1)!;
    expect(messages[0].updateDataModel.value).toEqual({ payload: PAYLOAD, scenario: {}, reviewed });

    // Re-mount → the review is injected back as reviewedSettings.
    unmount();
    sendNotificationSpy.mockClear();
    render(<ObligationArtefactTab surfaceId="rv1" />);
    act(() => capturedProps?.onInitialized?.());
    expect(lastNotification()[1].reviewedSettings).toEqual(reviewed);
  });

  it("a what-if reset preserves the reviewed overlay", () => {
    render(<ObligationArtefactTab surfaceId="rv2" />);
    act(() => capturedProps?.onInitialized?.());

    const reviewed = { policy: { cureDays: 28 } };
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: { x: 1 }, reviewed }));
    act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.reset" }));

    // Scenario dropped, review kept.
    const resetCall = appendMessagesSpy.mock.calls.at(-1);
    expect(resetCall?.[1][0].updateDataModel.value).toEqual({ payload: PAYLOAD, reviewed });
  });

  it("an empty reviewed overlay (panel reset-to-extracted) is not injected on mount", () => {
    surfaceRoot = { payload: PAYLOAD, reviewed: {} };
    render(<ObligationArtefactTab surfaceId="rv3" />);
    act(() => capturedProps?.onInitialized?.());
    expect(lastNotification()[1].reviewedSettings).toBeUndefined();
  });

  it("rehydrates a backend-persisted reviewed overlay from the surface data model", () => {
    const reviewed = { policy: { penPerDay: 5000 } };
    surfaceRoot = { payload: PAYLOAD, reviewed };
    render(<ObligationArtefactTab surfaceId="rv4" />);
    act(() => capturedProps?.onInitialized?.());
    expect(lastNotification()[1].reviewedSettings).toEqual(reviewed);
  });

  it("restores a backend-rehydrated scenario from the surface data model when no in-session store exists", () => {
    const scenario = { payDelta: { COD: -5 } };
    surfaceRoot = { payload: PAYLOAD, scenario };
    render(<ObligationArtefactTab surfaceId="s4" />);
    act(() => capturedProps?.onInitialized?.());
    const [, params] = lastNotification();
    expect(params.savedScenario).toEqual(scenario);
  });

  // ── serve-api engine-placement fallback flag ──────────────────────────────

  it("omits the engine config by default (in-browser WASM placement)", () => {
    render(<ObligationArtefactTab surfaceId="s5" />);
    act(() => capturedProps?.onInitialized?.());
    expect(lastNotification()[1].engine).toBeUndefined();
  });

  it("forwards the serve-api engine URL when NEXT_PUBLIC_OBLIGATION_ENGINE_URL is set", () => {
    vi.stubEnv("NEXT_PUBLIC_OBLIGATION_ENGINE_URL", "https://deontic.example/analyze");
    try {
      render(<ObligationArtefactTab surfaceId="s6" />);
      act(() => capturedProps?.onInitialized?.());
      expect(lastNotification()[1].engine).toEqual({
        mode: "serve-api",
        url: "https://deontic.example/analyze",
      });
    } finally {
      vi.unstubAllEnvs();
    }
  });

  // ── backend stash-update hook (hard-refresh persistence) ──────────────────

  describe("surface-data persistence", () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    function firstPost() {
      const [url, init] = fetchWithAuthSpy.mock.calls[0] as unknown as [string, RequestInit];
      return { url, body: JSON.parse(String(init.body)) };
    }

    it("debounce-POSTs the full data-model root to /surface-data on a scenario change", () => {
      render(<ObligationArtefactTab surfaceId="sd1" sessionId="sess-9" />);
      act(() => capturedProps?.onInitialized?.());

      const scenario = { deadlineDelta: { COD: 14 } };
      act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario }));
      expect(fetchWithAuthSpy).not.toHaveBeenCalled(); // still in the debounce window

      act(() => vi.advanceTimersByTime(800));
      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
      const { url, body } = firstPost();
      expect(url).toBe("/api/proxy/api/sessions/sess-9/surface-data");
      expect(body).toEqual({ surfaceId: "sd1", dataModel: { payload: PAYLOAD, scenario } });
    });

    it("coalesces rapid what-if edits into one POST carrying the LAST scenario", () => {
      render(<ObligationArtefactTab surfaceId="sd2" sessionId="sess-9" />);
      act(() => capturedProps?.onInitialized?.());

      for (const days of [1, 2, 3]) {
        act(() =>
          capturedProps?.onUpdateModelContext({
            kind: "obligation.recompute",
            scenario: { deadlineDelta: { COD: days } },
          }),
        );
        act(() => vi.advanceTimersByTime(100)); // within the debounce window
      }
      act(() => vi.advanceTimersByTime(800));

      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
      expect(firstPost().body.dataModel.scenario).toEqual({ deadlineDelta: { COD: 3 } });
    });

    it("re-posts the root without the scenario slot on a what-if reset", () => {
      render(<ObligationArtefactTab surfaceId="sd3" sessionId="sess-9" />);
      act(() => capturedProps?.onInitialized?.());
      act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.reset" }));
      act(() => vi.advanceTimersByTime(800));

      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
      // Scenario dropped (no review here) → the canonical what-if rehydrates.
      expect(firstPost().body).toEqual({ surfaceId: "sd3", dataModel: { payload: PAYLOAD } });
    });

    it("debounce-POSTs the reviewed overlay alongside the scenario", () => {
      render(<ObligationArtefactTab surfaceId="sd6" sessionId="sess-9" />);
      act(() => capturedProps?.onInitialized?.());

      const reviewed = { policy: { cureDays: 28 } };
      act(() =>
        capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: {}, reviewed }),
      );
      act(() => vi.advanceTimersByTime(800));

      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
      expect(firstPost().body).toEqual({
        surfaceId: "sd6",
        dataModel: { payload: PAYLOAD, scenario: {}, reviewed },
      });
    });

    it("flushes a pending edit on unmount instead of dropping it", () => {
      const { unmount } = render(<ObligationArtefactTab surfaceId="sd4" sessionId="sess-9" />);
      act(() => capturedProps?.onInitialized?.());
      const scenario = { fmLenDelta: 5 };
      act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario }));

      unmount(); // tab switch mid-debounce
      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
      expect(firstPost().body.dataModel.scenario).toEqual(scenario);

      // The debounce timer was cancelled — no duplicate send later.
      act(() => vi.advanceTimersByTime(2000));
      expect(fetchWithAuthSpy).toHaveBeenCalledTimes(1);
    });

    it("does not POST when no sessionId is provided (in-session persistence only)", () => {
      render(<ObligationArtefactTab surfaceId="sd5" />);
      act(() => capturedProps?.onInitialized?.());
      act(() => capturedProps?.onUpdateModelContext({ kind: "obligation.recompute", scenario: { x: 1 } }));
      act(() => vi.advanceTimersByTime(2000));
      expect(fetchWithAuthSpy).not.toHaveBeenCalled();
    });
  });
});
