// ChatShell — `?doc=` opens a document handed over from a page with no
// workbench of its own (a project home, projects design §6.2): it mounts as an
// included tab, like a sidebar click, and the params are dropped from the URL
// so a reload or the session writeback doesn't re-open it.

import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import type { UseSkillAgentReturn } from "@/hooks/useSkillAgent";

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

const mockReplace = vi.fn();
let query = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => query.get(k), toString: () => query.toString() }),
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


beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn());
});

describe("ChatShell — ?doc= from a project home", () => {
  it("mounts the document as a tab by its friendly name and cleans the URL", async () => {
    query = new URLSearchParams({ doc: "d-uuid-1", docName: "EDP contract.pdf", docFormat: "pdf", keep: "1" });
    await renderChat();
    expect(await screen.findAllByText("EDP contract.pdf")).not.toHaveLength(0);
    expect(mockReplace).toHaveBeenCalledWith("/chat/@user-1/test-slug?keep=1");
  });

  it("does nothing without the param", async () => {
    query = new URLSearchParams();
    await renderChat();
    expect(mockReplace).not.toHaveBeenCalledWith(expect.stringContaining("doc="));
  });
});
