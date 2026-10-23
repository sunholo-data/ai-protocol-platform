import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Folder, ParsedDocument } from "@/hooks/useDocBrowser";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/components/document/DocumentPreview", () => ({
  DocumentPreview: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));

import { fetchWithAuth } from "@/lib/apiClient";
import { __resetProjectsStore } from "@/hooks/useProjects";
import { DocListItem } from "@/components/doc-browser/DocListItem";
import { DocListFolder } from "@/components/doc-browser/DocListFolder";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

function route(handlers: Record<string, (init?: RequestInit) => Response | Promise<Response>>) {
  mockFetch.mockImplementation(async (url: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url)}`;
    const h = handlers[key];
    if (!h) throw new Error(`unexpected ${key}`);
    return h(init);
  });
}

const doc: ParsedDocument = {
  id: "d-uuid-1",
  originalFilename: "EDP contract.pdf",
  sourceFormat: "pdf",
  parseStatus: "parsed",
  parseError: null,
  folderId: "f1",
  userId: "u1",
  blockCount: 3,
  hasA2ui: false,
};
const iberia = { id: "p1", name: "Iberia portfolio review", docCount: 3, chatCount: 0, lastOpenedAt: "2026-10-20T09:00:00Z" };
const dispute = { id: "p2", name: "Settlement dispute", docCount: 1, chatCount: 0, lastOpenedAt: "2026-10-19T09:00:00Z" };

beforeEach(() => {
  __resetProjectsStore();
  mockFetch.mockReset();
  mockPush.mockReset();
});
afterEach(() => __resetProjectsStore());

// Submenu items are clicked with fireEvent: userEvent moves the pointer off the
// sub-trigger first, and with no layout in jsdom Radix reads that as leaving
// the submenu and closes it before the click lands.
async function openProjectSubmenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Document actions" }));
  await user.click(await screen.findByRole("menuitem", { name: /Add to project/ }));
}

describe("document ⋯ → Add to project…", () => {
  it("doesn't fetch projects until the submenu is opened", () => {
    render(<DocListItem doc={doc} onClick={vi.fn()} />);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("ticks the projects it is in; ticking another adds it and says so", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [iberia, dispute] }),
      "POST /api/proxy/api/projects/p2/documents": () => res(200, { added: [doc.id], already: [], failed: [] }),
    });
    render(<DocListItem doc={{ ...doc, projectIds: ["p1"] }} onClick={vi.fn()} />);
    await openProjectSubmenu(user);
    expect(await screen.findByRole("menuitemcheckbox", { name: "Remove from Iberia portfolio review" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Add to Settlement dispute" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Added to Settlement dispute"));
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", "/projects/p2/chat");
  });

  it("unticking removes it — and says the document is kept", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }),
      "DELETE /api/proxy/api/projects/p1/documents/d-uuid-1": () => res(204),
    });
    render(<DocListItem doc={{ ...doc, projectIds: ["p1"] }} onClick={vi.fn()} />);
    await openProjectSubmenu(user);
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Remove from Iberia portfolio review" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Removed from Iberia portfolio review — the document is kept."));
  });

  it("a failed add is an alert, not a silent no-op", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }),
      "POST /api/proxy/api/projects/p1/documents": () => res(500),
    });
    render(<DocListItem doc={doc} onClick={vi.fn()} />);
    await openProjectSubmenu(user);
    fireEvent.click(await screen.findByRole("menuitemcheckbox", { name: "Add to Iberia portfolio review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't add to the project (HTTP 500).");
  });

  it("+ New project… (E3) creates it with this document, pre-filled, and stays put", async () => {
    const user = userEvent.setup();
    let body: unknown;
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [] }),
      "POST /api/proxy/api/projects": (init) => {
        body = JSON.parse(String(init?.body));
        return res(201, { id: "p9", name: "EDP contract" });
      },
    });
    render(<DocListItem doc={doc} onClick={vi.fn()} />);
    await openProjectSubmenu(user);
    fireEvent.click(await screen.findByRole("menuitem", { name: "+ New project…" }));
    const input = await screen.findByRole("textbox", { name: "New project name" });
    expect(input).toHaveValue("EDP contract");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Added to EDP contract"));
    expect(body).toEqual({ name: "EDP contract", docIds: [doc.id], uniqueName: true });
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("+ New project… says so if the project was made but the document couldn't be added", async () => {
    const user = userEvent.setup();
    route({
      "GET /api/proxy/api/projects": () => res(200, { projects: [] }),
      "POST /api/proxy/api/projects": () =>
        res(201, { id: "p9", name: "EDP contract", documents: { added: [], already: [], failed: [{ id: doc.id, filename: "", reason: "It was deleted." }] } }),
    });
    render(<DocListItem doc={doc} onClick={vi.fn()} />);
    await openProjectSubmenu(user);
    fireEvent.click(await screen.findByRole("menuitem", { name: "+ New project…" }));
    await screen.findByRole("textbox", { name: "New project name" });
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Created EDP contract, but couldn't add EDP contract.pdf: It was deleted.");
  });

  it("shows read-only project chips by name, never by id", async () => {
    route({ "GET /api/proxy/api/projects": () => res(200, { projects: [iberia] }) });
    render(<DocListItem doc={{ ...doc, projectIds: ["p1"] }} onClick={vi.fn()} />);
    const chip = await screen.findByTestId("project-chips");
    expect(chip).toHaveAccessibleName("In project Iberia portfolio review");
    expect(chip).not.toHaveTextContent("p1");
  });
});

describe("folder ⋯ → Start a project from this folder (E4)", () => {
  const folder: Folder = { id: "f1", name: "PPAs 2025", userId: "u1", docCount: 4, parsedCount: 4 };

  it("creates from the folder and lands on the new home", async () => {
    const user = userEvent.setup();
    let body: unknown;
    route({
      "POST /api/proxy/api/projects": (init) => {
        body = JSON.parse(String(init?.body));
        return res(201, { id: "p5", name: "PPAs 2025" });
      },
    });
    render(<DocListFolder folder={folder} documents={[]} isActive={false} onSelect={vi.fn()} onDocClick={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Folder actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Start a project from this folder" }));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/projects/p5"));
    expect(body).toEqual({ name: "PPAs 2025", fromFolderId: "f1", uniqueName: true });
  });

  it("a failure is shown on the folder", async () => {
    const user = userEvent.setup();
    route({
      "POST /api/proxy/api/projects": () => res(409, { detail: { code: "PROJECT_LIMIT", message: "You have 100 projects already." } }),
    });
    render(<DocListFolder folder={folder} documents={[]} isActive={false} onSelect={vi.fn()} onDocClick={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Folder actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Start a project from this folder" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You have 100 projects already.");
    expect(mockPush).not.toHaveBeenCalled();
  });
});
