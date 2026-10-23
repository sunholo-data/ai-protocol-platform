// ChatShell — project scope (projects design §6.3): `?project=&ask=` from a
// project home starts a scoped chat and sends the question with
// forwardedProps.project_id; a resumed chat takes its row's project; the
// banner names the project only once it has loaded, and a deleted project
// drops the scope. Chatting in a project IS this screen: its chats in the
// sidebar and on the landing, its documents in the workbench, and an explicit
// way out.

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
    model: "",
    voice: null,
    a2ui: { allowActionTriggeredRuns: false },
    loading: false,
  }),
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";
import { __resetProjectsStore } from "@/hooks/useProjects";
import ChatPage from "@/app/chat/[...path]/page";

const paramsPromise = Promise.resolve({ path: ["@user-1", "test-slug"] });

async function renderChat() {
  await act(async () => {
    render(<ChatPage params={paramsPromise} />);
  });
  await waitFor(() => expect(subscribers.length).toBeGreaterThan(0));
}

const iberia = {
  id: "p1",
  name: "Iberia portfolio review",
  docCount: 12,
  chatCount: 3,
  lastOpenedAt: "2026-10-20T09:00:00Z",
  instructions: "Quote clause numbers.",
  defaultSkillId: null,
};

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
  routes = {};
  sendMessage = vi.fn().mockResolvedValue(undefined) as unknown as typeof sendMessage;
  vi.mocked(useSkillAgent).mockReturnValue(makeReturn({ sendMessage }));
});

describe("ChatShell — a chat scoped to a project", () => {
  it("?project=&ask= sends the question scoped to the project and cleans the URL", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    query = new URLSearchParams({ project: "p1", ask: "What are the curtailment terms?" });
    await renderChat();
    await waitFor(() =>
      expect(sendMessage).toHaveBeenCalledWith(
        "What are the curtailment terms?",
        expect.objectContaining({ projectId: "p1" }),
      ),
    );
    expect(mockReplace).toHaveBeenCalledWith("/chat/@user-1/test-slug");
    const scope = await screen.findByTestId("project-scope");
    expect(scope).toHaveTextContent(/In project\s*Iberia portfolio review\s*·\s*12 documents\s*·\s*instructions on/);
  });

  it("every later turn carries the scope too", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    await screen.findByTestId("project-scope");
    const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    fireEvent.change(box, { target: { value: "And the change-of-law clause?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(sendMessage).toHaveBeenCalledWith("And the change-of-law clause?", expect.objectContaining({ projectId: "p1" })),
    );
  });

  it("a resumed chat takes its row's project", async () => {
    routes["GET /api/proxy/api/sessions/s-old"] = () => res(200, { session: { document_ids: [], project_id: "p1" } });
    routes["GET /api/proxy/api/sessions/s-old/messages"] = () => res(200, { messages: [] });
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    query = new URLSearchParams({ session: "s-old" });
    await renderChat();
    expect(await screen.findByTestId("project-scope")).toHaveTextContent("Iberia portfolio review");
  });

  it("a deleted project drops the scope and claims nothing", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(404, { detail: { code: "PROJECT_NOT_FOUND", message: "Project not found." } });
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    await waitFor(() => expect(mockReplace).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByTestId("project-scope")).toBeNull();
    const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls.at(-1)?.[1]?.projectId ?? null).toBeNull();
  });

  it("a normal new chat is unscoped", async () => {
    query = new URLSearchParams();
    await renderChat();
    const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls.at(-1)?.[1]?.projectId ?? null).toBeNull();
    expect(screen.queryByTestId("project-scope")).toBeNull();
  });

  it("the composer asks about the project", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    await screen.findByTestId("project-scope");
    const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    expect(box).toHaveAttribute("placeholder", "Ask about Iberia portfolio review…");
  });

  it("Leave (the banner's ✕) unscopes: a plain new chat, and the next turn carries no project", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    const leave = (await screen.findAllByRole("button", { name: "Leave Iberia portfolio review" }))[0];
    query = new URLSearchParams();
    fireEvent.click(leave);
    await waitFor(() => expect(screen.queryByTestId("project-scope")).toBeNull());
    const box = screen.getAllByRole("textbox").find((el) => el.tagName === "TEXTAREA")!;
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    expect(sendMessage.mock.calls.at(-1)?.[1]?.projectId ?? null).toBeNull();
  });
});

describe("ChatShell — the chat screen is the project", () => {
  const projectChats = {
    sessions: [
      {
        session_id: "sess-d",
        skill_id: "test-skill-id",
        title: "EDP curtailment",
        last_message_at: new Date().toISOString(),
        membership: "derived",
        linkedDocument: { id: "d1", filename: "EDP.pdf" },
      },
    ],
    next_cursor: null,
  };

  it("the sidebar lists the PROJECT's chats, says so, and Show all chats leaves the project", async () => {
    window.localStorage.setItem("aitana.sidebar.open", "1");
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    routes["GET /api/proxy/api/projects/p1/sessions?page_size=30"] = () => res(200, projectChats);
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    const list = await screen.findByTestId("project-chats");
    expect(list).toHaveTextContent("In Iberia portfolio review");
    expect(await within(list).findByText("used EDP.pdf")).toBeInTheDocument();
    expect(within(list).getByRole("button", { name: /EDP curtailment/ })).toBeInTheDocument();
    query = new URLSearchParams();
    fireEvent.click(within(list).getByRole("button", { name: "Show all chats" }));
    await waitFor(() => expect(screen.queryByTestId("project-chats")).toBeNull());
  });

  it("a project's chats that fail to load say so, with Retry", async () => {
    window.localStorage.setItem("aitana.sidebar.open", "1");
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    routes["GET /api/proxy/api/projects/p1/sessions?page_size=30"] = () => res(500);
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    const list = await screen.findByTestId("project-chats");
    expect(await within(list).findByRole("alert")).toHaveTextContent("Couldn't load your conversations.");
    expect(within(list).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("the workbench shows the project's documents, not all recent ones", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, iberia);
    routes["GET /api/proxy/api/projects/p1/documents"] = () =>
      res(200, { documents: [{ id: "d1", originalFilename: "EDP.pdf", sourceFormat: "pdf", createdAt: "2026-10-01T00:00:00Z" }], next_cursor: null });
    routes["GET /api/proxy/api/documents/recent?limit=6"] = () =>
      res(200, { documents: [{ id: "x9", originalFilename: "unrelated.docx", sourceFormat: "docx", parseStatus: "parsed", lastUsedAt: "2026-10-02T00:00:00Z", lastSessionId: null, lastSessionTitle: null }] });
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    expect(await screen.findByRole("heading", { name: "Iberia portfolio review: documents" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Open EDP.pdf" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Open unrelated.docx" })).toBeNull();
  });

  it("an empty project says so in the workbench rather than showing demos", async () => {
    routes["GET /api/proxy/api/projects/p1"] = () => res(200, { ...iberia, docCount: 0 });
    routes["GET /api/proxy/api/projects/p1/documents"] = () => res(200, { documents: [], next_cursor: null });
    query = new URLSearchParams({ project: "p1" });
    await renderChat();
    expect(await screen.findByTestId("recent-documents-empty")).toHaveTextContent("No documents in this project yet.");
  });
});
