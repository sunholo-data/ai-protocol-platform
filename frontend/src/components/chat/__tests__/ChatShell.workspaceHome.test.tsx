// ChatShell — Workspace Home persistence (v6.23.0 B6).
//
// a tester, acceptance testing 2026-08-06, raised four times — more than any other single
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
    modelEvents: [],
    modelWait: null,
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

// A skill shaped like the tenant's front door: ships example prompts (a tester's
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
    // "Chart DK1 market prices" is verbatim the tile a tester clicked in the UAT.
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

// UI round 2: the workbench's tabs are the results. The old Home ("Workspace")
// tab is now "Start" — a skill's launchers and examples — and exists only when
// the skill has some; its results index moved to the All results menu.
// Example prompts sit in one place at a time: chips under the landing composer
// on a fresh chat, the Start tab once the conversation is going.
const midConversation = () =>
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ messages: [{ id: "m1", role: "user", content: "hello" }] }));

async function openAllResults() {
  const trigger = screen.getByRole("button", { name: /All results/i });
  await act(async () => {
    trigger.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0 }));
  });
  return screen.findByRole("menu");
}

describe("ChatShell — the workbench's tabs are the results (B6 kept)", () => {
  it("keeps the examples on Start after a result artifact arrives — THE regression guard", async () => {
    midConversation();
    await renderChat();
    expect(screen.getByTestId("example-prompts")).toBeTruthy();

    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    // Still mounted, one click away on Start — not destroyed by the result.
    expect(screen.getByTestId("example-prompts")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Start/i })).toBeTruthy();
  });

  it("on a fresh chat shows the prompts as chips under the composer, and no Start tab for prompts alone", async () => {
    await renderChat();
    const chips = screen.getByRole("list", { name: "Example prompts" });
    expect(chips.textContent).toContain("Chart DK1 market prices");
    expect(screen.queryByTestId("example-prompts")).toBeNull();
    expect(screen.queryByRole("tab", { name: /Start/i })).toBeNull();
  });

  it("promotes the dominant workspace surface to its own closable Result tab", async () => {
    await renderChat();
    expect(screen.queryByRole("tab", { name: /Assistant/i })).toBeNull();

    emitSurface(workspaceEnvelope);

    const tab = await screen.findByRole("tab", { name: /Assistant/i });
    // Repo principle #7 — auto-focus new workbench elements.
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("button", { name: /close .*assistant/i })).toBeTruthy();
  });

  it("never auto-focuses Start away from a result — Start is furniture, not a target", async () => {
    midConversation();
    await renderChat();
    emitSurface(workspaceEnvelope);
    await screen.findByRole("tab", { name: /Assistant/i });

    expect(screen.getByRole("tab", { name: /Start/i }).getAttribute("aria-selected")).toBe("false");
    emitSurface(workspaceEnvelope);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Start/i }).getAttribute("aria-selected")).toBe("false"),
    );
  });

  it("All results lists both artifact results and the promoted workspace surface", async () => {
    await renderChat();
    emitSurface(pricesEnvelope);
    emitSurface(workspaceEnvelope);
    await screen.findByRole("tab", { name: /Assistant/i });

    // Every result stays reachable after its tab is closed — the promoted
    // surface included — by friendly title (CLAUDE.md #9, never a surfaceId).
    const menu = await openAllResults();
    expect(menu.textContent).toContain("DE_LU prices");
    expect(menu.textContent).toContain("Assistant");
    expect(menu.textContent).toContain("Activity log");
    expect(menu.textContent).not.toContain("entsoe_prices");
  });

  it("keeps focus on Start when the SAME result surface is re-emitted (B4, integration half)", async () => {
    // See src/lib/__tests__/workbenchFocus.test.ts for the blink itself.
    midConversation();
    await renderChat();
    emitSurface(pricesEnvelope);
    const result = await screen.findByRole("tab", { name: /DE_LU prices/i });
    expect(result.getAttribute("aria-selected")).toBe("true");

    const start = screen.getByRole("tab", { name: /Start/i });
    act(() => {
      start.click();
    });
    await waitFor(() => expect(start.getAttribute("aria-selected")).toBe("true"));

    emitSurface(pricesEnvelope);
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Start/i }).getAttribute("aria-selected")).toBe("true"),
    );
    expect(screen.getAllByRole("tab", { name: /DE_LU prices/i })).toHaveLength(1);
  });

  it("still auto-focuses a genuinely NEW result while another is open (#7 intact)", async () => {
    midConversation();
    await renderChat();
    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    const start = screen.getByRole("tab", { name: /Start/i });
    act(() => {
      start.click();
    });
    await waitFor(() => expect(start.getAttribute("aria-selected")).toBe("true"));

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
    // A specialist can't route a front door's tiles anywhere (CLAUDE.md #8).
    skillMeta.canDelegate = false;
    midConversation();
    await renderChat();

    emitSurface(pricesEnvelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    await waitFor(() => expect(screen.queryByTestId("example-prompts")).toBeNull());
    // No Start tab to show, and the result is still listed — never a blank pane.
    expect(screen.queryByRole("tab", { name: /Start/i })).toBeNull();
    const menu = await openAllResults();
    expect(menu.textContent).toContain("DE_LU prices");
  });
});
