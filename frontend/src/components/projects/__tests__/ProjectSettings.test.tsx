import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));
vi.mock("@/components/document/DocumentThumbnail", () => ({ DocumentThumbnail: () => <div data-testid="thumb" /> }));
vi.mock("@/components/doc-browser/UploadDropZone", () => ({
  UploadDropZone: ({ onUploadComplete }: { onUploadComplete?: (id: string, name: string) => void }) => (
    <button type="button" onClick={() => onUploadComplete?.("d-up", "new.pdf")}>
      Upload
    </button>
  ),
}));
const skills = [
  { skillId: "s1", slug: "ppa-expert", ownerId: "platform", displayName: "PPA Expert", name: "ppa-expert" },
  { skillId: "s2", slug: "general", ownerId: "platform", displayName: "General Assistant", name: "general" },
];
vi.mock("@/hooks/useUserSkills", () => ({ useUserSkills: () => ({ skills, isLoading: false, error: null }) }));
vi.mock("@/hooks/useTenantDefaultSkill", () => ({ useTenantDefaultSkill: () => "s2" }));

import { fetchWithAuth } from "@/lib/apiClient";
import { ProjectSettings, ProjectSearchLine } from "../ProjectSettings";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

type Handler = (init?: RequestInit) => Response | Promise<Response>;
let handlers: Record<string, Handler>;
function route(extra: Record<string, Handler>) {
  handlers = { ...handlers, ...extra };
}

const project = {
  id: "p1",
  name: "Iberia portfolio review",
  docCount: 1,
  chatCount: 1,
  lastOpenedAt: "2026-10-20T09:00:00Z",
  instructions: "",
  defaultSkillId: "s1",
};
const P = "/api/proxy/api/projects/p1";

beforeEach(() => {
  mockFetch.mockReset();
  mockPush.mockReset();
  handlers = {
    [`GET ${P}`]: () => res(200, project),
    [`GET ${P}/documents`]: () => res(200, { documents: [], next_cursor: null }),
    [`GET ${P}/sessions?page_size=30`]: () => res(200, { sessions: [], next_cursor: null }),
    "GET /api/proxy/api/skills/s1": () => res(200, { welcome: null }),
  };
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    const h = handlers[key];
    if (!h) throw new Error(`unexpected ${key}`);
    return h(init);
  });
});

const CHAT = "/chat/@platform/ppa-expert";
const withChat = () =>
  route({
    [`GET ${P}/sessions?page_size=30`]: () =>
      res(200, {
        sessions: [{ session_id: "sess-1", skill_id: "s1", title: "EDP", last_message_at: new Date().toISOString(), membership: "explicit" }],
        next_cursor: null,
      }),
  });

describe("ProjectSettings — loading states", () => {
  it("a deleted project says so, with the way back", async () => {
    route({ [`GET ${P}`]: () => res(404, { detail: "Not found" }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByText("This project was deleted.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See your projects" })).toHaveAttribute("href", "/projects");
  });

  it("a load failure offers Retry and Back", async () => {
    let calls = 0;
    route({ [`GET ${P}`]: () => (++calls === 1 ? res(500) : res(200, project)) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByText("Couldn't open this project.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back" })).toHaveAttribute("href", "/projects");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Iberia portfolio review" })).toBeInTheDocument();
  });

  it("a documents load failure is visible with Retry", async () => {
    route({ [`GET ${P}/documents`]: () => res(500) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByText("Couldn't load this project's documents.")).toBeInTheDocument();
  });
});

describe("ProjectSettings — a settings page, not a chat page", () => {
  it("a new project reads 'Set up your project', with the guide and a way to start chatting", async () => {
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Set up your project" })).toBeInTheDocument();
    const guide = await screen.findByRole("region", { name: "Getting started" });
    expect(guide).toHaveTextContent("Add the documents you're working with");
    expect(screen.getByTestId("project-docs-empty")).toHaveTextContent("No documents yet.");
    expect(screen.getByRole("link", { name: "Start chatting in this project" })).toHaveAttribute("href", `${CHAT}?project=p1`);
  });

  it("has no composer and no chat list — chatting happens on the chat screen", async () => {
    withChat();
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByRole("heading", { level: 1, name: "Project settings" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Ask about/ })).toBeNull();
    expect(screen.queryByRole("list", { name: "Project chats" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Getting started" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open project chats" })).toHaveAttribute("href", `${CHAT}?project=p1`);
    expect(screen.getByRole("link", { name: /Back to Iberia portfolio review chats/ })).toHaveAttribute("href", `${CHAT}?project=p1`);
  });

  it("the guide ticks off documents once some are added", async () => {
    route({
      [`GET ${P}/documents`]: () =>
        res(200, { documents: [{ id: "d1", originalFilename: "EDP.pdf", sourceFormat: "pdf", folderId: "f1", projectIds: ["p1"] }], next_cursor: null }),
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    // Documents but no chats yet: the guide stays, with its first step done,
    // and the page is now "Project settings".
    const guide = await screen.findByRole("region", { name: "Getting started" });
    await waitFor(() => expect(guide).toHaveTextContent("(done)"));
    expect(screen.getByRole("heading", { level: 1, name: "Project settings" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open project chats" })).toBeInTheDocument();
  });

  it("says chats use the instructions, and never asks the user to make anything searchable", async () => {
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByText(/Every chat in this project uses them/)).toBeInTheDocument();
    expect(screen.queryByText(/Make searchable/i)).toBeNull();
  });
});

describe("ProjectSettings — documents", () => {
  it("opens a document in a chat in this project, on its assistant", async () => {
    route({
      [`GET ${P}/documents`]: () =>
        res(200, { documents: [{ id: "d1", originalFilename: "EDP.pdf", sourceFormat: "pdf", createdAt: "2026-10-01T00:00:00Z" }], next_cursor: null }),
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Open EDP.pdf" }));
    expect(mockPush).toHaveBeenCalledWith(`${CHAT}?doc=d1&docName=EDP.pdf&docFormat=pdf&project=p1`);
  });

  it("Remove from project shows pending, and an error if it fails", async () => {
    let resolve!: (r: Response) => void;
    route({
      [`GET ${P}/documents`]: () => res(200, { documents: [{ id: "d1", originalFilename: "EDP.pdf", sourceFormat: "pdf" }], next_cursor: null }),
      [`DELETE ${P}/documents/d1`]: () => new Promise<Response>((r) => (resolve = r)),
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Remove EDP.pdf from project" }));
    const btn = screen.getByRole("button", { name: "Remove EDP.pdf from project" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Removing…");
    await act(async () => resolve(res(500)));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't remove it from the project (HTTP 500).");
  });

  it("an upload here is tagged into the project, and says so", async () => {
    route({ [`POST ${P}/documents`]: () => res(200, { added: ["d-up"], already: [], failed: [] }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await userEvent.click(await screen.findByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Added new.pdf")).toBeInTheDocument();
  });
});

describe("ProjectSettings — search status (passive, optional)", () => {
  it("shows nothing when the backend doesn't report search (public template)", async () => {
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("project-search")).toBeNull();
  });

  it("shows nothing when search is unavailable", async () => {
    route({ [`GET ${P}`]: () => res(200, { ...project, search: { indexed: 0, failed: 0, total: 3, status: "unavailable" } }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("project-search")).toBeNull();
  });

  it("says 'Ready for search' when ready", async () => {
    route({ [`GET ${P}`]: () => res(200, { ...project, search: { indexed: 3, failed: 0, total: 3, status: "ready" } }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    const line = await screen.findByTestId("project-search");
    expect(line).toHaveTextContent("Ready for search");
    expect(line).not.toHaveTextContent("can't be searched");
  });

  it("notes, without asking anything, documents that can't be searched", async () => {
    route({ [`GET ${P}`]: () => res(200, { ...project, search: { indexed: 4, failed: 2, total: 6, status: "ready" } }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    const line = await screen.findByTestId("project-search");
    expect(line).toHaveTextContent("Ready for search");
    expect(line).toHaveTextContent("2 documents can't be searched (no readable text)");
    expect(within(line).queryByRole("button")).toBeNull();
    expect(within(line).queryByRole("link")).toBeNull();
  });

  it("treats search: null (the public template) as nothing to show", async () => {
    route({ [`GET ${P}`]: () => res(200, { ...project, search: null }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByTestId("project-search")).toBeNull();
  });

  it("says 'Preparing N of M for search…' while indexing", async () => {
    route({ [`GET ${P}`]: () => res(200, { ...project, search: { indexed: 1, failed: 0, total: 3, status: "indexing" } }) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    expect(await screen.findByTestId("project-search")).toHaveTextContent("Preparing 1 of 3 for search…");
  });
});

describe("ProjectSearchLine — polling", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("re-reads every 10s while indexing, then says it's still preparing after a few minutes", () => {
    vi.useFakeTimers();
    const onPoll = vi.fn();
    render(<ProjectSearchLine search={{ indexed: 1, failed: 0, total: 3, status: "indexing" }} onPoll={onPoll} />);
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(onPoll).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(3 * 60_000);
    });
    expect(screen.getByTestId("project-search")).toHaveTextContent("Still preparing for search");
    const calls = onPoll.mock.calls.length;
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(onPoll).toHaveBeenCalledTimes(calls);
  });

  it("does not poll once ready", () => {
    vi.useFakeTimers();
    const onPoll = vi.fn();
    render(<ProjectSearchLine search={{ indexed: 3, failed: 0, total: 3, status: "ready" }} onPoll={onPoll} />);
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(onPoll).not.toHaveBeenCalled();
  });
});

describe("ProjectSettings — editing", () => {
  it("renames in place; a clash is shown in the server's words", async () => {
    const user = userEvent.setup();
    route({
      [`PATCH ${P}`]: () => res(409, { detail: { code: "PROJECT_EXISTS", message: "You already have a project called 'Dispute'." } }),
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await user.click(await screen.findByRole("button", { name: "Iberia portfolio review" }));
    const input = screen.getByRole("textbox", { name: "Project name" });
    await user.clear(input);
    await user.type(input, "Dispute{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("You already have a project called 'Dispute'.");
  });

  it("instructions save on blur, visibly", async () => {
    const user = userEvent.setup();
    let body: unknown;
    route({
      [`PATCH ${P}`]: (init) => {
        body = JSON.parse(String(init?.body));
        return res(200, { ...project, instructions: "Quote clause numbers." });
      },
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await user.type(await screen.findByRole("textbox", { name: "Project instructions" }), "Quote clause numbers.");
    fireEvent.blur(screen.getByRole("textbox", { name: "Project instructions" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(body).toEqual({ instructions: "Quote clause numbers." });
  });

  it("the default assistant is picked by name and saved", async () => {
    const user = userEvent.setup();
    let body: unknown;
    route({
      [`PATCH ${P}`]: (init) => {
        body = JSON.parse(String(init?.body));
        return res(200, { ...project, defaultSkillId: "s2" });
      },
    });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await user.selectOptions(await screen.findByRole("combobox", { name: "New chats open on" }), "General Assistant");
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(body).toEqual({ defaultSkillId: "s2" });
  });

  it("delete says documents and chats are kept and memories go, then goes to the list", async () => {
    const user = userEvent.setup();
    route({ [`DELETE ${P}`]: () => res(204) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await user.click(await screen.findByRole("button", { name: "Delete project…" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete project?" });
    expect(dialog).toHaveTextContent("Your documents and chats are kept.");
    expect(dialog).toHaveTextContent("remembers from this project");
    await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects"));
  });

  it("a failed delete keeps the dialog open with the error", async () => {
    const user = userEvent.setup();
    route({ [`DELETE ${P}`]: () => res(500) });
    render(<ProjectSettings projectId="p1" uid="u1" />);
    await user.click(await screen.findByRole("button", { name: "Delete project…" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete project?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Couldn't delete the project (HTTP 500).");
    expect(mockPush).not.toHaveBeenCalled();
  });
});
