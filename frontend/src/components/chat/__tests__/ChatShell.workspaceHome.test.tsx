// ChatShell — Workspace Home persistence (v6.23.0 B6).
//
// Dana, ONE UAT 2026-08-06, raised four times — more than any other single
// item in the meeting:
//
//   "You click on 'chart DK1 market prices' and now you have Workspace
//    results... it would be nice to ALSO have the list of skills this assistant
//    can do for other iterations, INSTEAD OF CREATING A NEW CHAT."
//
// The defect: the Workspace tab did two unrelated jobs — "here is what you can
// start" and "here is what came back" — and the second evicted the first
// (`showHome = !workspaceHasContent && hasArtifacts`, plus an artifactCount
// gate on the launcher). Starting a second skill therefore cost a new chat,
// which compounded the context loss tracked in v6.23.0 #1.
//
// These tests drive the REAL chain, not the branch in isolation, because
// "A2UI won't render in the Workspace" is the repo's recurring bug class:
//
//   A2UI_SURFACE CUSTOM event → WorkspaceA2uiEventRouter →
//   SurfaceRegistry.appendMessages → useArtifacts()/useSurfaceState →
//   workbench tab assembly
//
// SCOPE / HONESTY: jsdom green is NOT proof this renders live (both CLAUDE.mds).
// What it proves is the tab-assembly half — given the real event on the wire,
// Home survives and the result gets its own tab. The live half is the manual
// gate in docs/design/v6.23.0/workspace-home-persistence.md.

import { render, screen, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { UseSkillAgentReturn } from "@/hooks/useSkillAgent";

import pricesEnvelope from "./fixtures/entsoe-prices-a2ui-surface.json";

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

// A skill shaped like ONE's front door: ships example prompts (Dana's
// "chart DK1 market prices" tiles) AND can delegate, so the picker persists.
// `canDelegate` is flipped per-test to cover the specialist case.
const skillMeta = {
  displayName: "Test Skill",
  ownerId: "test-owner",
  slug: "test-slug",
  mcpServerIds: [],
  initialMessage: "",
  welcome: {
    exampleDocuments: [],
    // The real ExamplePrompt shape (src/types/skill.ts) — `label`, not `title`.
    // "Chart DK1 market prices" is verbatim the tile Dana clicked in the UAT.
    examplePrompts: [
      { label: "Chart DK1 market prices", prompt: "Chart DK1 market prices for last week" },
      { label: "Compare two PPAs", prompt: "Compare these two PPA contracts" },
    ],
  },
  model: "",
  voice: null,
  a2ui: { allowActionTriggeredRuns: false },
  tools: [] as string[],
  canDelegate: true,
  loading: false,
};

vi.mock("@/hooks/useSkillMeta", () => ({
  useSkillMeta: () => skillMeta,
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";
import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

async function renderChat() {
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  await waitFor(() => expect(subscribers.length).toBeGreaterThan(0));
}

function emitSurface(value: unknown) {
  act(() => {
    for (const handler of subscribers) handler({ event: { name: "A2UI_SURFACE", value } });
  });
}

/** A bare `workspace` surface — the dominant-surface case that used to clobber
 * Home. No `artifact` metadata, so `listArtifacts()` never returns it: this is
 * exactly the one result kind that was exempt from the 7.5 tab model. */
const workspaceEnvelope = {
  surfaceId: "workspace",
  messages: [
    {
      version: "v0.9",
      createSurface: {
        surfaceId: "workspace",
        catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json",
      },
    },
    {
      version: "v0.9",
      updateSurface: {
        surfaceId: "workspace",
        components: [
          { id: "root", componentType: "Box", children: ["t1"] },
          { id: "t1", componentType: "Text", text: "Dominant workspace output" },
        ],
        root: "root",
      },
    },
  ],
};

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  skillMeta.canDelegate = true;
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn());
});

describe("ChatShell — Workspace Home persistence (B6)", () => {
  it("keeps the examples picker on Home after a result artifact arrives — THE regression guard", async () => {
    // A front door, mid-conversation: the exact state where the tiles used to
    // vanish and the only way back was a new chat.
    vi.mocked(useSkillAgent).mockReturnValue(
      makeReturn({ messages: [{ id: "m1", role: "user", content: "hello" }] }),
    );
    await renderChat();
    expect(screen.getByTestId("example-prompts")).toBeTruthy();

    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    // Before this change the picker was gone the moment a result landed, and the
    // only route back to it was a NEW CHAT. It must still be mounted — one click
    // away on Home, not destroyed.
    expect(screen.getByTestId("example-prompts")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Workspace/i })).toBeTruthy();
  });

  it("keeps the picker on Home after a DOMINANT workspace surface arrives", async () => {
    // The harder case: a bare `workspace` surface carries no artifact metadata,
    // so it can't ride the 7.5 artifact-tab model. It used to render INSIDE the
    // Workspace tab, replacing Home outright.
    await renderChat();
    emitSurface(workspaceEnvelope);

    await screen.findByRole("tab", { name: /Assistant/i });
    expect(screen.getByTestId("example-prompts")).toBeTruthy();
  });

  it("promotes the dominant workspace surface to its own closable Result tab", async () => {
    await renderChat();
    expect(screen.queryByRole("tab", { name: /Assistant/i })).toBeNull();

    emitSurface(workspaceEnvelope);

    const tab = await screen.findByRole("tab", { name: /Assistant/i });
    // Repo principle #7 — auto-focus new workbench elements. The user still
    // lands on the result; the difference is that Home survives.
    expect(tab.getAttribute("aria-selected")).toBe("true");
    // Every Result tab is closable; the structural tabs (Home/Document/Activity)
    // are not. The promoted surface joins the result family, close included.
    expect(screen.getByRole("button", { name: /close .*assistant/i })).toBeTruthy();
  });

  it("never auto-focuses Home away from a result — Home is furniture, not a target", async () => {
    // B4 ("Workspace tab sometimes needs a second click") is a focus-stealing
    // shape: before this change an arriving workspace surface called
    // onWorkbenchTabChange("workspace"), the SAME id the user clicks to reach
    // Home. A click could therefore be undone by an in-flight surface. Now the
    // surface targets its own tab id, so Home is never contested.
    await renderChat();
    emitSurface(workspaceEnvelope);
    await screen.findByRole("tab", { name: /Assistant/i });

    const home = screen.getByRole("tab", { name: /Workspace/i });
    expect(home.getAttribute("aria-selected")).toBe("false");

    // A second surface update must not yank focus onto Home.
    emitSurface(workspaceEnvelope);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Workspace/i }).getAttribute("aria-selected")).toBe(
        "false",
      ),
    );
  });

  it("Home indexes both artifact results and the promoted workspace surface", async () => {
    await renderChat();
    emitSurface(pricesEnvelope);
    emitSurface(workspaceEnvelope);
    await screen.findByRole("tab", { name: /Assistant/i });

    // Home is a navigation index (WorkbenchHome), so both results must be
    // reachable from it — the promoted surface included, or it would be the one
    // result you can't get back to after closing its tab.
    const home = screen.getByRole("tab", { name: /Workspace/i });
    act(() => {
      home.click();
    });
    await waitFor(() => expect(home.getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByTestId("example-prompts")).toBeTruthy();
    // Both results listed by friendly title (CLAUDE.md #9 — never a surfaceId).
    const index = await screen.findByTestId("workbench-index");
    expect(index.textContent).toContain("DE_LU prices");
    expect(index.textContent).toContain("Assistant");
    expect(index.textContent).not.toContain("entsoe_prices");
  });

  it("keeps focus on Home when the SAME result surface is re-emitted (B4, integration half)", async () => {
    // B4 — "Workspace tab sometimes needs a second click" (Dana; Mark had seen
    // it too). Reproduced live 2026-08-07: with a prices result open and
    // auto-focused, clicking Workspace mid-run showed Home, and by the time the
    // run finished focus was back on the Result tab.
    //
    // HONEST SCOPE: this test does NOT reproduce the underlying blink — a
    // re-emission through the event router keeps `state.surface` non-null
    // throughout, so the artifact never leaves `listArtifacts()`. Verified: it
    // passes against the pre-fix implementation too. What it guards is the
    // weaker, still-worth-having property that an ordinary re-emission does not
    // move the user's focus.
    //
    // The blink itself is guarded where it is actually decidable, as a pure
    // rule: src/lib/__tests__/workbenchFocus.test.ts, "does NOT re-focus after a
    // surface blinks out and returns". That one DOES fail against the old code.
    await renderChat();
    emitSurface(pricesEnvelope);
    const result = await screen.findByRole("tab", { name: /DE_LU prices/i });
    expect(result.getAttribute("aria-selected")).toBe("true");

    const home = screen.getByRole("tab", { name: /Workspace/i });
    act(() => {
      home.click();
    });
    await waitFor(() => expect(home.getAttribute("aria-selected")).toBe("true"));

    // The re-registration. No NEW result — the same surfaceId again.
    emitSurface(pricesEnvelope);

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Workspace/i }).getAttribute("aria-selected")).toBe(
        "true",
      ),
    );
    // Still exactly one result tab — nothing was added, so nothing justified a
    // focus change.
    expect(screen.getAllByRole("tab", { name: /DE_LU prices/i })).toHaveLength(1);
  });

  it("still auto-focuses a genuinely NEW result while another is open (#7 intact)", async () => {
    // The B4 fix must not blunt auto-focus: a second, different result still
    // takes the stage even though the user is sitting on Home.
    await renderChat();
    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    const home = screen.getByRole("tab", { name: /Workspace/i });
    act(() => {
      home.click();
    });
    await waitFor(() => expect(home.getAttribute("aria-selected")).toBe("true"));

    emitSurface({
      ...pricesEnvelope,
      surfaceId: "web_sources",
      artifact: { kind: "sources", title: "Sources", description: "3 sources" },
      messages: pricesEnvelope.messages.map((m) =>
        JSON.parse(JSON.stringify(m).replaceAll("entsoe_prices", "web_sources")),
      ),
    });

    const sources = await screen.findByRole("tab", { name: /Sources/i });
    await waitFor(() => expect(sources.getAttribute("aria-selected")).toBe("true"));
  });

  it("does NOT keep tiles up for a skill that cannot delegate", async () => {
    // A specialist answers its own prompts; it cannot route to another skill.
    // Persisting a front door's tile list there would advertise handoffs it
    // can't perform — a dead end (CLAUDE.md #8). Specialists keep the original
    // first-turn-only onboarding picker, so once a result lands it is gone.
    skillMeta.canDelegate = false;
    // A conversation already under way — isFreshChat is false, which is the
    // specialist's fallback gate.
    vi.mocked(useSkillAgent).mockReturnValue(
      makeReturn({ messages: [{ id: "m1", role: "user", content: "hello" }] }),
    );
    await renderChat();

    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    // The tiles must be gone — this skill cannot route them anywhere.
    await waitFor(() => expect(screen.queryByTestId("example-prompts")).toBeNull());
    // Home still exists and still indexes the result — it is never a blank pane.
    const home = screen.getByRole("tab", { name: /Workspace/i });
    act(() => {
      home.click();
    });
    await waitFor(() => expect(home.getAttribute("aria-selected")).toBe("true"));
    expect((await screen.findByTestId("workbench-index")).textContent).toContain("DE_LU prices");
  });

  it("renders Home content, never a blank pane, with no results at all", async () => {
    // NEVER SILENT (#8): Home always has something to show. With no artifacts
    // and no launcher, that is the picker.
    await renderChat();
    const home = screen.getByRole("tab", { name: /Workspace/i });
    expect(home.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("example-prompts")).toBeTruthy();
  });
});
