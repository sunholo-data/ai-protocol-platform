import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import type { Project } from "@/hooks/useProjects";
import { ProjectKnowledgeBases } from "../ProjectKnowledgeBases";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

const project: Project = {
  id: "p1",
  name: "Client A",
  docCount: 3,
  chatCount: 0,
  lastOpenedAt: null,
  instructions: "",
  defaultSkillId: null,
  attachedKbIds: [],
};

let patches: unknown[];

beforeEach(() => {
  mockFetch.mockReset();
  patches = [];
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    if (key === "GET /api/proxy/api/knowledge-bases") {
      return res(200, [
        { kbId: "method", name: "Method notes", description: "How we run engagements" },
        { kbId: "docs", name: "Product docs" },
      ]);
    }
    if (key === "PATCH /api/proxy/api/projects/p1") {
      const body = JSON.parse(String(init?.body));
      patches.push(body);
      if (body.attachedKbIds.includes("docs")) {
        return res(422, { detail: { code: "KNOWLEDGE_BASE_NOT_ATTACHABLE", message: "You can only attach knowledge bases you can search." } });
      }
      return res(200, { ...project, attachedKbIds: body.attachedKbIds });
    }
    throw new Error(`unexpected ${key}`);
  });
});

describe("ProjectKnowledgeBases", () => {
  it("attaches a knowledge base and says so", async () => {
    render(<ProjectKnowledgeBases project={project} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: /Method notes/ }));
    expect(await screen.findByText("Method notes is searched in this project's chats.")).toBeTruthy();
    expect(patches).toEqual([{ attachedKbIds: ["method"] }]);
    expect((screen.getByRole("checkbox", { name: /Method notes/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("shows a refusal and leaves the box unticked", async () => {
    render(<ProjectKnowledgeBases project={project} />);
    await userEvent.click(await screen.findByRole("checkbox", { name: /Product docs/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("You can only attach knowledge bases you can search.");
    expect((screen.getByRole("checkbox", { name: /Product docs/ }) as HTMLInputElement).checked).toBe(false);
  });

  it("offers to remove an attachment the user can no longer search", async () => {
    render(<ProjectKnowledgeBases project={{ ...project, attachedKbIds: ["method", "retired"] }} />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove it" }));
    await waitFor(() => expect(patches).toEqual([{ attachedKbIds: ["method"] }]));
  });

  it("is absent where the deployment has no knowledge bases", async () => {
    mockFetch.mockImplementation(async () => res(404));
    const { container } = render(<ProjectKnowledgeBases project={project} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("never reports attached knowledge bases as gone when the list fails to load", async () => {
    let calls = 0;
    mockFetch.mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return res(503);
      return res(200, [{ kbId: "method", name: "Method notes" }]);
    });
    render(<ProjectKnowledgeBases project={{ ...project, attachedKbIds: ["method"] }} />);
    expect((await screen.findByRole("alert")).textContent).toContain("Couldn't load knowledge bases.");
    expect(screen.queryByRole("button", { name: "Remove it" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(((await screen.findByRole("checkbox", { name: /Method notes/ })) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByRole("button", { name: "Remove it" })).toBeNull();
  });
});
