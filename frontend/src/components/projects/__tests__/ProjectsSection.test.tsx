import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));

import { fetchWithAuth } from "@/lib/apiClient";
import { __resetProjectsStore } from "@/hooks/useProjects";
import { DOC_DRAG_TYPE } from "@/components/doc-browser/folderActions";
import { ProjectsSection, SIDEBAR_PROJECT_LIMIT } from "../ProjectsSection";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

const iberia = { id: "p1", name: "Iberia portfolio review", docCount: 3, chatCount: 2, lastOpenedAt: "2026-10-20T09:00:00Z" };

/** Route by method + path so each test only states what matters. */
function route(handlers: Record<string, (init?: RequestInit) => Response | Promise<Response>>) {
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    const h = handlers[key];
    if (!h) throw new Error(`unexpected ${key}`);
    return h(init);
  });
}

function drop(target: HTMLElement, payload: object) {
  const dataTransfer = {
    types: [DOC_DRAG_TYPE],
    getData: (t: string) => (t === DOC_DRAG_TYPE ? JSON.stringify(payload) : ""),
    dropEffect: "none",
  };
  fireEvent.dragOver(target, { dataTransfer });
  fireEvent.drop(target, { dataTransfer });
  return dataTransfer;
}

beforeEach(() => {
  __resetProjectsStore();
  mockFetch.mockReset();
  mockPush.mockReset();
});
afterEach(() => __resetProjectsStore());

/** Where a project row opens: its chat, scoped (ChatShell supplies the real one). */
const chatHref = (p: { id: string }) => `/chat/s1?project=${p.id}`;

describe("ProjectsSection — states (NEVER SILENT)", () => {
  it("shows skeletons on the first load", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    render(<ProjectsSection chatHrefFor={chatHref} />);
    expect(screen.getByRole("status", { name: "Loading your projects" })).toBeInTheDocument();
  });

  it("a load failure says so, with Retry", async () => {
    route({ "GET /api/proxy/api/projects": () => res(503) });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your projects.");
    route({ "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }) });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Iberia portfolio review")).toBeInTheDocument();
  });

  it("with no projects it explains itself and still offers + New project (E2)", async () => {
    route({ "GET /api/proxy/api/projects": () => res(200, { projects: [] }) });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    expect(await screen.findByTestId("projects-empty")).toHaveTextContent("Group the documents and chats you're working on.");
    expect(screen.getByRole("button", { name: "New project" })).toBeInTheDocument();
  });

  it(`lists at most ${SIDEBAR_PROJECT_LIMIT}, each opening its chats, with settings and See all`, async () => {
    const many = Array.from({ length: 10 }, (_, i) => ({ ...iberia, id: `p${i}`, name: `Project ${i}` }));
    route({ "GET /api/proxy/api/projects": () => res(200, { projects: many }) });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    const list = await screen.findByRole("list", { name: "Your projects" });
    expect(within(list).getAllByRole("link", { name: /^Project \d+\s*\d/ })).toHaveLength(SIDEBAR_PROJECT_LIMIT);
    expect(within(list).getAllByRole("link", { name: /^Project \d+\s*\d/ })[0]).toHaveAttribute("href", "/chat/s1?project=p0");
    expect(within(list).getAllByRole("link", { name: /settings$/ })[0]).toHaveAttribute("href", "/projects/p0");
    expect(screen.getByRole("link", { name: "See all (10)" })).toHaveAttribute("href", "/projects");
  });
});

describe("ProjectsSection — + New project (E1)", () => {
  it("creates from the inline field and lands on the new home", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [] }),
      "POST /api/proxy/api/projects": () => res(201, { id: "new1", name: "Settlement dispute" }),
    });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    await user.click(await screen.findByRole("button", { name: "New project" }));
    await user.type(screen.getByRole("textbox", { name: "New project name" }), "Settlement dispute{Enter}");
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects/new1"));
  });

  it("shows a name clash in the server's own words and stays open", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }),
      "POST /api/proxy/api/projects": () =>
        res(409, { detail: { code: "PROJECT_EXISTS", message: "You already have a project called 'Iberia portfolio review'." } }),
    });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    await user.click(await screen.findByRole("button", { name: "New project" }));
    await user.type(screen.getByRole("textbox", { name: "New project name" }), "Iberia portfolio review{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("You already have a project called 'Iberia portfolio review'.");
    expect(screen.getByRole("textbox", { name: "New project name" })).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("an empty name is refused before any request", async () => {
    const user = userEvent.setup();
    route({ "GET /api/proxy/api/projects": () => res(200, { projects: [] }) });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    await user.click(await screen.findByRole("button", { name: "New project" }));
    await user.type(screen.getByRole("textbox", { name: "New project name" }), "   {Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Give the project a name.");
  });
});

describe("ProjectsSection — dropping a document ADDS it", () => {
  async function renderWithIberia(post: () => Response | Promise<Response>) {
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }),
      "POST /api/proxy/api/projects/p1/documents": post,
    });
    render(<ProjectsSection chatHrefFor={chatHref} />);
    const link = await screen.findByRole("link", { name: /^Iberia portfolio review\s*\d/ });
    return link.closest("li") as HTMLElement;
  }

  it("says Adding…, then Added to X — as a copy, not a move", async () => {
    let resolve!: (r: Response) => void;
    const row = await renderWithIberia(() => new Promise<Response>((r) => (resolve = r)));
    const dt = drop(row, { id: "d1", folderId: "f1", filename: "EDP.pdf" });
    expect(dt.dropEffect).toBe("copy");
    expect(await screen.findByText("Adding EDP.pdf…")).toBeInTheDocument();
    await act(async () => resolve(res(200, { added: ["d1"], already: [], failed: [] })));
    expect(await screen.findByText("Added to Iberia portfolio review")).toBeInTheDocument();
    const post = mockFetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ doc_ids: ["d1"] });
  });

  it("says Already in X rather than nothing", async () => {
    const row = await renderWithIberia(() => res(200, { added: [], already: ["d1"], failed: [] }));
    drop(row, { id: "d1", filename: "EDP.pdf" });
    expect(await screen.findByText("Already in Iberia portfolio review")).toBeInTheDocument();
  });

  it("an error stays visible with Retry", async () => {
    let calls = 0;
    const row = await renderWithIberia(() => {
      calls += 1;
      return calls === 1 ? res(500) : res(200, { added: ["d1"], already: [], failed: [] });
    });
    drop(row, { id: "d1", filename: "EDP.pdf" });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't add to the project (HTTP 500).");
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Added to Iberia portfolio review")).toBeInTheDocument();
  });

  it("ignores drags that aren't documents", async () => {
    const row = await renderWithIberia(() => res(200, {}));
    fireEvent.drop(row, { dataTransfer: { types: ["text/plain"], getData: () => "hello" } });
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
