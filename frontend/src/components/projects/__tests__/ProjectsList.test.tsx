import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));
// Which assistant a project opens on is resolved from the user's skills; the
// list only needs a link per project.
vi.mock("@/hooks/useProjectChatHref", () => ({
  useProjectChatHref: () => (p: { id: string }) => `/chat/s1?project=${p.id}`,
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { __resetProjectsStore } from "@/hooks/useProjects";
import { ProjectsList } from "../ProjectsList";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

beforeEach(() => {
  __resetProjectsStore();
  mockFetch.mockReset();
  mockPush.mockReset();
});
afterEach(() => __resetProjectsStore());

describe("/projects", () => {
  it("lists every project with its counts; each opens its chats, settings are secondary", async () => {
    mockFetch.mockResolvedValueOnce(
      res(200, { projects: [{ id: "p1", name: "Iberia portfolio review", docCount: 3, chatCount: 1, lastOpenedAt: "2026-10-20T09:00:00Z" }] }),
    );
    render(<ProjectsList uid="u1" />);
    const link = await screen.findByRole("link", { name: /^Iberia portfolio review\s*3 documents/ });
    expect(link).toHaveAttribute("href", "/chat/s1?project=p1");
    expect(screen.getByRole("link", { name: "Iberia portfolio review settings" })).toHaveAttribute("href", "/projects/p1");
    expect(link).toHaveTextContent("3 documents · 1 chat");
  });

  it("an empty list invites the first project, and creating lands on its settings (set-up)", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce(res(200, { projects: [] }));
    render(<ProjectsList uid="u1" />);
    await user.click(await screen.findByRole("button", { name: "Create your first project" }));
    mockFetch.mockResolvedValueOnce(res(201, { id: "p7", name: "Dispute" }));
    mockFetch.mockResolvedValue(res(200, { projects: [] }));
    await user.type(screen.getByRole("textbox", { name: "New project name" }), "Dispute{Enter}");
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects/p7"));
  });

  it("a load failure is visible with Retry", async () => {
    mockFetch.mockResolvedValueOnce(res(500));
    render(<ProjectsList uid="u1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your projects.");
  });
});
