// ChatShell — the composer's Model pick (docs/design/v6.42.0/model-choice.md): a new
// chat sends Auto; a reopened chat shows and keeps the pick saved on its row,
// and sends nothing until that row has loaded; the chip's × goes back to Auto.

import { render, screen, act, waitFor, fireEvent, within } from "@testing-library/react";
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

// Every backend call ChatShell makes goes through fetchWithAuth; route the ones
// this test cares about, and answer the rest with an empty 404.
type Handler = () => Response;
let routes: Record<string, Handler> = {};
function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}
vi.mock("@/lib/apiClient", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/apiClient")>();
  return {
    ...mod,
    fetchWithAuth: vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(url)}`;
      return routes[key]?.() ?? res(404);
    }),
  };
});

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
    model: "pro",
    thinking: "dynamic",
    modelChoice: true,
    canDelegate: false,
    voice: null,
    a2ui: { allowActionTriggeredRuns: false },
    loading: false,
  }),
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";
import { __resetProjectsStore } from "@/hooks/useProjects";
import { resetModelMenuCache } from "@/lib/modelChoice";
import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

async function renderChat() {
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  await waitFor(() => expect(subscribers.length).toBeGreaterThan(0));
}

let sendMessage: ReturnType<typeof vi.fn> & UseSkillAgentReturn["sendMessage"];
beforeEach(() => {
  // Deterministic in-memory localStorage (jsdom's is flaky across pool modes);
  // the sidebar tests open it through the remembered "aitana.sidebar.open".
  const store: Record<string, string> = {};
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = v;
    },
    removeItem: (k: string) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
    key: (i: number) => Object.keys(store)[i] ?? null,
    get length() {
      return Object.keys(store).length;
    },
  });
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo;
  subscribers.length = 0;
  vi.clearAllMocks();
  __resetProjectsStore();
  resetModelMenuCache();
  routes = { "GET /api/proxy/api/models/choice": () => res(200, MENU) };
  sendMessage = vi.fn().mockResolvedValue(undefined) as unknown as typeof sendMessage;
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
});

const MENU = {
  offered: "all",
  levels: {
    fast: { model: "gemini-3-5-flash-lite", label: "Gemini 3.5 Flash-Lite" },
    thorough: { model: "claude-opus-5-5", label: "Claude Opus 5.5" },
  },
  models: [{ id: "gemini-3-8-flash", label: "Gemini 3.8 Flash", note: "balanced" }],
  labels: { pro: "Gemini 3.8 Flash", "gemini-3-8-flash": "Gemini 3.8 Flash" },
  resolved: { pro: "gemini-3-8-flash" },
};

function send(text: string) {
  const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

describe("ChatShell — the composer's Model pick", () => {
  it("a new chat is on Auto: no chip, and nothing extra on the wire", async () => {
    query = new URLSearchParams();
    await renderChat();
    send("Hello");
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith("Hello", expect.anything()));
    expect(sendMessage.mock.calls.at(-1)?.[1]?.modelChoice).toBeUndefined();
    expect(screen.queryByRole("button", { name: /^Use Auto instead of/ })).toBeNull();
  });

  it("a reopened chat shows its saved pick and keeps sending it", async () => {
    routes["GET /api/proxy/api/sessions/s-old"] = () => res(200, { session: { document_ids: [], model_choice: "thorough" } });
    routes["GET /api/proxy/api/sessions/s-old/messages"] = () => res(200, { messages: [] });
    query = new URLSearchParams({ session: "s-old" });
    await renderChat();
    expect(await screen.findByRole("button", { name: "Use Auto instead of Thorough" })).toBeInTheDocument();
    send("Go deeper");
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith("Go deeper", expect.objectContaining({ modelChoice: "thorough" })));
  });

  it("the chip's × goes back to Auto, and the next turn clears the saved pick", async () => {
    routes["GET /api/proxy/api/sessions/s-old"] = () => res(200, { session: { document_ids: [], model_choice: "model:gemini-3-8-flash" } });
    routes["GET /api/proxy/api/sessions/s-old/messages"] = () => res(200, { messages: [] });
    query = new URLSearchParams({ session: "s-old" });
    await renderChat();
    fireEvent.click(await screen.findByRole("button", { name: "Use Auto instead of Gemini 3.8 Flash" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Use Auto instead of/ })).toBeNull());
    send("Plain again");
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith("Plain again", expect.objectContaining({ modelChoice: "auto" })));
  });

  it("a reopened chat whose row hasn't loaded sends no pick, so it can't wipe the saved one", async () => {
    // No route for the session row: it never loads.
    routes["GET /api/proxy/api/sessions/s-slow/messages"] = () => res(200, { messages: [] });
    query = new URLSearchParams({ session: "s-slow" });
    await renderChat();
    send("Quick one");
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls.at(-1)?.[1]?.modelChoice).toBeUndefined();
  });
});
