// ChatShell — prices artifact wiring (PRICES-WORKSPACE M4).
//
// "A2UI won't render in the Workspace" is THE recurring bug in this repo, so
// this test exercises the REAL chain rather than asserting the branch in
// isolation:
//
//   A2UI_SURFACE CUSTOM event (the VERBATIM envelope the backend transform
//   emits) → WorkspaceA2uiEventRouter → SurfaceRegistry.appendMessages(…,
//   artifact) → useArtifacts() → workbench tab → SeriesArtefactTab
//
// The fixture below is NOT hand-written: it is the literal output of
// `render_for_emit("entsoe_day_ahead_prices", <result>)` from
// backend/adk/a2ui_entsoe_render.py, captured 2026-07-17. If the backend
// envelope shape drifts (renamed `artifact.kind`, a different surfaceId, a
// restructured dataModel), this test goes red — which is the point. Regenerate
// it from the transform, never edit it by hand to make the test pass.
//
// SCOPE / HONESTY: jsdom green is NOT proof this renders live (repo playbooks,
// both CLAUDE.mds). What this DOES prove is the frontend half — given the real
// event on the wire, a tab appears, auto-focuses, and mounts the series tab.
// The backend half (that the event actually reaches the wire) is proven by a
// real stream, which is M6's gate.

import { render, screen, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { UseSkillAgentReturn } from "@/hooks/useSkillAgent";

import envelope from "./fixtures/entsoe-prices-a2ui-surface.json";

// ── Mocks: the minimum to render ChatPage in jsdom (mirrors
// src/__tests__/chat-error-display.test.tsx — the established pattern). ───────

function makeReturn(overrides: Partial<UseSkillAgentReturn> = {}): UseSkillAgentReturn {
  return {
    sessionId: "test-thread",
    messages: [],
    toolCalls: [],
    thinkingContent: "",
    isThinking: false,
    stageLabel: null,
    delegations: [],
    fallbacks: [],
    compactions: [],
    tidyingUp: false,
    resolvedModel: null,
    sendMessage: vi.fn().mockResolvedValue(undefined),
    isLoading: false,
    error: null,
    clearError: vi.fn(),
    stop: vi.fn(),
    ...overrides,
  };
}

vi.mock("@/hooks/useSkillAgent", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/hooks/useSkillAgent")>();
  return { ...mod, useSkillAgent: vi.fn() };
});

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { uid: "test" }, loading: false }),
}));

// The AG-UI agent is the seam the WorkspaceA2uiEventRouter subscribes to.
// Capture the subscriber so the test can push a real A2UI_SURFACE event.
type CustomEventHandler = (e: { event: { name?: unknown; value?: unknown } }) => void;
const subscribers: CustomEventHandler[] = [];

vi.mock("@/providers/AGUIProvider", () => ({
  AGUIProvider: ({ children }: { children: React.ReactNode }) => children,
  useAGUIAgent: () => ({
    subscribe: ({ onCustomEvent }: { onCustomEvent?: CustomEventHandler }) => {
      if (onCustomEvent) subscribers.push(onCustomEvent);
      return { unsubscribe: () => {} };
    },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => ({ get: vi.fn().mockReturnValue(null) }),
}));

vi.mock("@/hooks/useSlugResolution", () => ({
  useSlugResolution: () => ({ skillId: "test-skill-id", loading: false, notFound: false, error: null }),
}));

vi.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => ({ ready: true, lastError: null }),
}));

vi.mock("@/hooks/useSkillMeta", () => ({
  useSkillMeta: () => ({
    displayName: "Test Skill",
    ownerId: "test-owner",
    slug: "test-slug",
    mcpServerIds: [],
    initialMessage: "",
    welcome: null,
    model: "",
    voice: null,
    a2ui: { allowActionTriggeredRuns: false },
    loading: false,
  }),
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";
import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

/** Render the chat page and wait for the A2UI event router to subscribe.
 * ChatPage resolves `params` asynchronously, so emitting before the router has
 * mounted drops the event on the floor (subscribers is still empty). */
async function renderChat() {
  // ChatPage `use()`s the params promise, so the initial render SUSPENDS. The
  // render must therefore happen inside an AWAITED act() — otherwise React
  // never retries the suspended render, the tree never commits, and the body
  // stays `<div />` forever (React says so explicitly: "A component suspended
  // inside an `act` scope, but the `act` call was not awaited").
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  // The event router subscribes on mount; nothing can be emitted before that.
  await waitFor(() => expect(subscribers.length).toBeGreaterThan(0));
}

/** Push the backend's A2UI_SURFACE event at every live router subscriber. */
function emitSurface(value: unknown) {
  act(() => {
    for (const handler of subscribers) handler({ event: { name: "A2UI_SURFACE", value } });
  });
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn());
});

describe("ChatShell — prices artifact (PRICES-WORKSPACE M4)", () => {
  it("the fixture is the shape the backend actually emits", () => {
    // Guards the two conditions this wiring depends on. If either drifts, the
    // tab silently disappears — so assert them rather than trusting the branch.
    expect(envelope.artifact.kind).toBe("prices");
    expect(envelope.surfaceId).toBe("entsoe_prices");
    // Trap 5: the surfaceId inside the messages must match the emitted surface.
    for (const msg of envelope.messages) {
      const body = Object.values(msg).find((v) => typeof v === "object" && v !== null);
      expect((body as { surfaceId?: string }).surfaceId).toBe(envelope.surfaceId);
    }
  });

  it("renders a workbench tab for a prices artifact and auto-focuses it", async () => {
    await renderChat();
    // Nothing before the event — the tab must come FROM the surface, not exist
    // speculatively.
    expect(screen.queryByRole("tab", { name: /DE_LU prices/i })).toBeNull();

    emitSurface(envelope);

    // (a) The artifact registered and became its own Result tab, titled from
    //     artifact.title — a friendly name, never the surfaceId (CLAUDE.md #9).
    const tab = await screen.findByRole("tab", { name: /DE_LU prices/i });
    // (b) Repo principle #7 — auto-focus, don't merely badge.
    expect(tab.getAttribute("aria-selected")).toBe("true");
  });

  it("mounts SeriesArtefactTab (not the generic A2UI fallback) for kind=prices", async () => {
    await renderChat();
    emitSurface(envelope);

    // The real SeriesArtefactTab is rendered here (not a stub), so this asserts
    // the branch AND that the tab can read the emitted data model.
    expect(await screen.findByTestId("series-stats")).toBeTruthy();
    // Server-computed stats are shown verbatim — the client never re-derives
    // them (the agent quotes the same numbers in prose).
    const stats = envelope.messages[2].updateDataModel!.value.stats;
    expect(stats.min).toBe(-7.6);
    expect(screen.getByTestId("series-stats").textContent).toContain("-7.6");
    // No raw surface id leaks into the UI.
    expect(screen.getByTestId("series-stats").textContent).not.toContain("entsoe_prices");
  });

  it("does not render the prices tab for an artifact of another kind", async () => {
    await renderChat();
    emitSurface({
      ...envelope,
      surfaceId: "web_sources",
      artifact: { kind: "sources", title: "Sources", description: "3 sources" },
      messages: envelope.messages.map((m) => JSON.parse(JSON.stringify(m).replaceAll("entsoe_prices", "web_sources"))),
    });
    await screen.findByRole("tab", { name: /Sources/i });
    expect(screen.queryByTestId("series-stats")).toBeNull();
  });
});
