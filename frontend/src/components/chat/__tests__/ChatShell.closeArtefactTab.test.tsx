// ChatShell — closable workbench artifact tabs (CLOSABLE-ARTEFACT-TABS).
//
// Result tabs accumulate with no way to remove them, and prices tabs are now
// PER-QUERY (`entsoe_prices:dk1:…`) so an analyst can compare DK1 vs DK2 —
// deliberate, but it makes proliferation unbounded (sweep 7 date ranges → 7
// tabs). This pins the close affordance end-to-end against the REAL chain:
//
//   A2UI_SURFACE CUSTOM event → WorkspaceA2uiEventRouter →
//   SurfaceRegistry.appendMessages → useArtifacts() → workbench tab → `×` →
//   window.confirm → SurfaceRegistry.clearSurface → tab gone
//
// It uses the same fixture + harness as ChatShell.pricesArtefact.test.tsx (the
// literal output of `render_for_emit(...)` from a2ui_entsoe_render.py) rather
// than a hand-rolled artifact, so the tabs under test are the ones users get.
//
// SCOPE / HONESTY: jsdom green is NOT proof this renders live (repo playbooks,
// both CLAUDE.mds). What this DOES prove is the frontend contract: which tabs
// offer a `×`, that confirm gates the removal, that accepting genuinely
// de-registers the artifact (count decrements — not merely hidden), that focus
// lands somewhere sensible, and that closing does not tombstone the surfaceId.

import { render, screen, act, waitFor, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { UseSkillAgentReturn } from "@/hooks/useSkillAgent";

import envelope from "./fixtures/entsoe-prices-a2ui-surface.json";

// ── Mocks: mirrors ChatShell.pricesArtefact.test.tsx (established pattern). ───

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

/** A second, independent prices artifact — the DK1-vs-DK2 compare case that
 * motivated per-query tabs (and therefore this feature). Rewrites the surfaceId
 * throughout the envelope so the messages still target their own surface
 * (Trap 5 in the A2UI playbook). */
function pricesEnvelope(surfaceId: string, title: string) {
  return {
    ...JSON.parse(JSON.stringify(envelope).replaceAll("entsoe_prices", surfaceId)),
    artifact: { kind: "prices", title, description: `${title} from BigQuery` },
    sourceId: `tool-${surfaceId}`,
  };
}

/** Result tabs only — the friendly labels of every artifact tab on screen. */
function resultTabNames(): string[] {
  return screen
    .getAllByRole("button", { name: /^Close / })
    .map((b) => b.getAttribute("aria-label")!.replace(/^Close /, ""));
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Mock window.confirm — matches the existing idiom in ChatShell
 * (handleDeleteSkillSession, the make-public gate). */
function stubConfirm(answer: boolean) {
  const confirm = vi.fn().mockReturnValue(answer);
  vi.stubGlobal("confirm", confirm);
  return confirm;
}

describe("ChatShell — closable artifact tabs (CLOSABLE-ARTEFACT-TABS)", () => {
  it("puts a × on artifact tabs but NOT on the fixed structural tabs", async () => {
    await renderChat();
    emitSurface(envelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    // The result is closable — named by its friendly title (CLAUDE.md #9).
    expect(screen.getByLabelText("Close DE_LU prices")).toBeTruthy();

    // Workspace/Home, Document and Activity are structural furniture, not
    // results. They must never offer a close.
    expect(screen.queryByLabelText("Close Workspace")).toBeNull();
    expect(screen.queryByLabelText("Close Document")).toBeNull();
    expect(screen.queryByLabelText("Close Activity")).toBeNull();
    // ...and those three ARE actually on screen (guards a vacuous pass above).
    for (const fixed of ["Workspace", "Document", "Activity"]) {
      expect(screen.getByRole("tab", { name: new RegExp(fixed, "i") })).toBeTruthy();
    }
    expect(resultTabNames()).toEqual(["DE_LU prices"]);
  });

  it("is generic across artifact kinds — a sources result is closable too", async () => {
    await renderChat();
    emitSurface({
      ...JSON.parse(JSON.stringify(envelope).replaceAll("entsoe_prices", "web_sources")),
      artifact: { kind: "sources", title: "Sources", description: "3 sources" },
    });
    await screen.findByRole("tab", { name: /Sources/i });
    // Nothing here is keyed on `kind` — every artifact tab gets the affordance.
    expect(screen.getByLabelText("Close Sources")).toBeTruthy();
  });

  it("CANCELLING the confirm keeps the tab (nothing is lost)", async () => {
    const confirm = stubConfirm(false);
    await renderChat();
    emitSurface(envelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    fireEvent.click(screen.getByLabelText("Close DE_LU prices"));

    expect(confirm).toHaveBeenCalledTimes(1);
    // The tab, its close affordance and its content all survive.
    expect(screen.getByRole("tab", { name: /DE_LU prices/i })).toBeTruthy();
    expect(screen.getByTestId("series-stats")).toBeTruthy();
    expect(resultTabNames()).toEqual(["DE_LU prices"]);
  });

  it("warns by FRIENDLY name that the result is lost and can't be undone", async () => {
    const confirm = stubConfirm(false);
    await renderChat();
    emitSurface(envelope);
    await screen.findByRole("tab", { name: /DE_LU prices/i });

    fireEvent.click(screen.getByLabelText("Close DE_LU prices"));

    const copy = confirm.mock.calls[0][0] as string;
    // Names the result the analyst recognises — never the surfaceId.
    expect(copy).toContain("DE_LU prices");
    expect(copy).not.toContain("entsoe_prices");
    // Says what is lost, and that it is irreversible — this result may have
    // cost a real BigQuery job.
    expect(copy).toMatch(/can't be undone/i);
    expect(copy).toMatch(/re-running the query/i);
  });

  it("ACCEPTING the confirm de-registers the artifact — the count decrements", async () => {
    const confirm = stubConfirm(true);
    await renderChat();
    // The DK1-vs-DK2 case: two independent per-query prices artifacts.
    emitSurface(pricesEnvelope("entsoe_prices_dk1", "DK1 prices"));
    emitSurface(pricesEnvelope("entsoe_prices_dk2", "DK2 prices"));
    await screen.findByRole("tab", { name: /DK2 prices/i });
    expect(resultTabNames()).toEqual(["DK1 prices", "DK2 prices"]);

    fireEvent.click(screen.getByLabelText("Close DK1 prices"));

    expect(confirm).toHaveBeenCalledTimes(1);
    // Gone from the registry (artifactCount decremented), not merely hidden:
    // the tab is absent from the DOM entirely, and the sibling is untouched.
    await waitFor(() => expect(resultTabNames()).toEqual(["DK2 prices"]));
    expect(screen.queryByRole("tab", { name: /DK1 prices/i })).toBeNull();
    expect(screen.getByRole("tab", { name: /DK2 prices/i })).toBeTruthy();
  });

  it("moves focus to Workspace when the CLOSED tab was the active one (#8 never silent)", async () => {
    stubConfirm(true);
    await renderChat();
    emitSurface(pricesEnvelope("entsoe_prices_dk1", "DK1 prices"));
    emitSurface(pricesEnvelope("entsoe_prices_dk2", "DK2 prices"));
    // The 7.5 auto-focus effect lands the user ON the newest artifact tab...
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /DK2 prices/i }).getAttribute("aria-selected")).toBe("true"),
    );

    // ...so closing THAT one must not leave a blank pane or a dead tab id.
    fireEvent.click(screen.getByLabelText("Close DK2 prices"));

    await waitFor(() => expect(screen.queryByRole("tab", { name: /DK2 prices/i })).toBeNull());
    // Focus falls back to Workspace/Home — NOT to the surviving DK1 tab, and
    // never to nothing. Home indexes what's left, so the user can get anywhere.
    expect(screen.getByRole("tab", { name: /Workspace/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /DK1 prices/i })).toBeTruthy();
  });

  it("closing the LAST artifact folds the workbench away — never a blank pane", async () => {
    stubConfirm(true);
    await renderChat();
    emitSurface(envelope);
    const tab = await screen.findByRole("tab", { name: /DE_LU prices/i });
    expect(tab.getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByLabelText("Close DE_LU prices"));

    // With no artifacts, no workspace surface, no open doc and no activity, the
    // pane has nothing worth showing, so the 2026-06-11 auto-fold retracts it
    // entirely and chat takes the full row. That is the sensible terminal state
    // (#8) — the alternative would be an empty workbench staring at the user.
    await waitFor(() => expect(screen.queryByRole("tab", { name: /DE_LU prices/i })).toBeNull());
    expect(screen.queryByRole("tablist", { name: /Workbench tabs/i })).toBeNull();
    // The chat itself is untouched and still usable.
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("leaves focus alone when closing a tab the user was NOT looking at", async () => {
    stubConfirm(true);
    await renderChat();
    emitSurface(pricesEnvelope("entsoe_prices_dk1", "DK1 prices"));
    emitSurface(pricesEnvelope("entsoe_prices_dk2", "DK2 prices"));
    // Auto-focus put us on DK2 (the newest).
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /DK2 prices/i }).getAttribute("aria-selected")).toBe("true"),
    );

    fireEvent.click(screen.getByLabelText("Close DK1 prices"));

    // Closing the OTHER tab must not yank the user off what they're reading.
    await waitFor(() => expect(screen.queryByRole("tab", { name: /DK1 prices/i })).toBeNull());
    expect(screen.getByRole("tab", { name: /DK2 prices/i }).getAttribute("aria-selected")).toBe("true");
  });

  it("does NOT tombstone: re-running the identical query re-registers the same surfaceId", async () => {
    stubConfirm(true);
    await renderChat();
    // An envelope with an explicit sourceId — appendMessages dedupes on it, so
    // this also pins that close CLEARS consumedToolCallIds. A stale dedupe entry
    // would silently swallow the re-emit and the tab would never come back.
    const dk1 = pricesEnvelope("entsoe_prices_dk1", "DK1 prices");
    emitSurface(dk1);
    await screen.findByRole("tab", { name: /DK1 prices/i });

    fireEvent.click(screen.getByLabelText("Close DK1 prices"));
    await waitFor(() => expect(screen.queryByRole("tab", { name: /DK1 prices/i })).toBeNull());

    // Same surfaceId, same sourceId — the identical query, re-run.
    emitSurface(dk1);

    const reborn = await screen.findByRole("tab", { name: /DK1 prices/i });
    // It renders normally: closable again, focused again (it's "new"), and its
    // content is really mounted — not an empty shell.
    expect(reborn.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByLabelText("Close DK1 prices")).toBeTruthy();
    expect(within(screen.getByRole("tabpanel", { hidden: false })).getByTestId("series-stats")).toBeTruthy();
  });
});
