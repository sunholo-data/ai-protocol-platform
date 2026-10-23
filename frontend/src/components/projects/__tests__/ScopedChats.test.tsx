// Projects P2 (projects design §4 E5, §6.3): a chat row's ⋯ menu ("Make this a
// project", "Move to project…") and the scoped-chat link.

import { useState } from "react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatSessionSummary } from "@/hooks/useSkillSessions";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));

import { fetchWithAuth } from "@/lib/apiClient";
import { __resetProjectsStore, useProject } from "@/hooks/useProjects";
import { SessionProjectMenu, SessionProjectNotice, type SessionProjectFeedback } from "../SessionProjectMenu";
import { projectChatHref } from "@/hooks/useProjectChatHref";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

type Handler = (init?: RequestInit) => Response | Promise<Response>;
let handlers: Record<string, Handler>;
const calls: Array<{ key: string; body: unknown }> = [];
beforeEach(() => {
  __resetProjectsStore();
  mockFetch.mockReset();
  mockPush.mockReset();
  calls.length = 0;
  handlers = {};
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const h = handlers[key];
    if (!h) throw new Error(`unexpected ${key}`);
    return h(init);
  });
});
afterEach(() => __resetProjectsStore());

const iberia = { id: "p1", name: "Iberia portfolio review", docCount: 3, chatCount: 1, lastOpenedAt: "2026-10-20T09:00:00Z" };
const dispute = { id: "p2", name: "Settlement dispute", docCount: 1, chatCount: 0, lastOpenedAt: "2026-10-19T09:00:00Z" };

const chat: ChatSessionSummary = {
  session_id: "s1",
  document_ids: ["d1"],
  skill_id: "sk",
  owner_uid: "u1",
  title: "EDP curtailment questions",
  turn_count: 4,
  first_message_at: "2026-10-20T08:00:00Z",
  last_message_at: "2026-10-20T08:10:00Z",
  archived_at: null,
  is_owner: true,
  project_id: null,
};

function Harness({ session = chat }: { session?: ChatSessionSummary }) {
  const [fb, setFb] = useState<SessionProjectFeedback | null>(null);
  return (
    <>
      <SessionProjectMenu session={session} onFeedback={setFb} />
      <SessionProjectNotice feedback={fb} onDismiss={() => setFb(null)} />
    </>
  );
}

describe("chat ⋯ → Make this a project", () => {
  it("creates a project from the chat (free name) and links to it", async () => {
    const user = userEvent.setup();
    handlers["POST /api/proxy/api/projects"] = () =>
      res(201, { ...iberia, id: "p9", name: "EDP curtailment questions (2)", documents: { added: ["d1"], already: [], failed: [] }, chatAdded: true });
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "More actions for EDP curtailment questions" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Make this a project" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Created EDP curtailment questions (2)"));
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/p9/chat");
    const post = calls.find((c) => c.key === "POST /api/proxy/api/projects");
    expect(post?.body).toEqual({ name: "EDP curtailment questions", fromSessionId: "s1", uniqueName: true });
  });

  it("shows the server's error text verbatim", async () => {
    const user = userEvent.setup();
    handlers["POST /api/proxy/api/projects"] = () =>
      res(409, { detail: { code: "PROJECT_LIMIT", message: "You have reached the limit of 100 projects." } });
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /More actions/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Make this a project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You have reached the limit of 100 projects.");
  });
});

describe("chat ⋯ → Move to project", () => {
  it("doesn't load projects until the menu opens", () => {
    render(<Harness />);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("lists projects, moves the chat, and says where", async () => {
    const user = userEvent.setup();
    handlers["GET /api/proxy/api/projects"] = () => res(200, { projects: [iberia, dispute] });
    handlers["PATCH /api/proxy/api/sessions/s1"] = () => res(200, { session: { ...chat, project_id: "p2" } });
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /More actions/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Settlement dispute" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Moved to Settlement dispute"));
    expect(calls.find((c) => c.key === "PATCH /api/proxy/api/sessions/s1")?.body).toEqual({ project_id: "p2" });
  });

  it("marks the current project and offers 'No project' to take it out", async () => {
    const user = userEvent.setup();
    handlers["GET /api/proxy/api/projects"] = () => res(200, { projects: [iberia, dispute] });
    handlers["PATCH /api/proxy/api/sessions/s1"] = () => res(200, {});
    render(<Harness session={{ ...chat, project_id: "p1" }} />);
    await user.click(screen.getByRole("button", { name: /More actions/ }));
    const current = await screen.findByRole("menuitem", { name: /Iberia portfolio review/ });
    expect(current).toHaveAttribute("data-disabled");
    fireEvent.click(screen.getByRole("menuitem", { name: "No project" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("is no longer in a project"));
    expect(calls.find((c) => c.key === "PATCH /api/proxy/api/sessions/s1")?.body).toEqual({ project_id: null });
  });

  it("a failed move shows the error", async () => {
    const user = userEvent.setup();
    handlers["GET /api/proxy/api/projects"] = () => res(200, { projects: [iberia] });
    handlers["PATCH /api/proxy/api/sessions/s1"] = () => res(404, { detail: { code: "PROJECT_NOT_FOUND", message: "Project not found." } });
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /More actions/ }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "Iberia portfolio review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Project not found.");
  });

  it("a project list that fails to load says so inside the menu", async () => {
    const user = userEvent.setup();
    handlers["GET /api/proxy/api/projects"] = () => res(500);
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /More actions/ }));
    expect(await screen.findByText("Couldn't load your projects.")).toBeInTheDocument();
  });
});

describe("scoped chat link", () => {
  it("builds the scoped chat URL, with the question when there is one", () => {
    expect(projectChatHref("/chat/s1", "p1")).toBe("/chat/s1?project=p1");
    expect(projectChatHref("/chat/s1", "p1", " What changed? ")).toBe("/chat/s1?project=p1&ask=What+changed%3F");
  });
});

describe("useProject(null)", () => {
  it("is idle and fetches nothing", async () => {
    const { result } = renderHook(() => useProject(null));
    expect(result.current.status).toBe("idle");
    await act(async () => {});
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
