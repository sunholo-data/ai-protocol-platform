import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Folder } from "@/hooks/useDocBrowser";
import type { KnowledgeBaseInfo } from "@/hooks/useKnowledgeBases";
import { DocListFolder, searchBadge } from "../DocListFolder";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/lib/documentEvents", () => ({ notifyDocumentsChanged: vi.fn(), subscribeDocumentsChanged: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

const llm: Folder = { id: "f-llm", name: "llm", userId: "u1", docCount: 11, parsedCount: 11 };
const kb: KnowledgeBaseInfo = { kbId: "llm-abc123", name: "llm", documentCount: 11, state: "done", total: 11, done: 11 };

function renderFolder(folder: Folder, over: Partial<React.ComponentProps<typeof DocListFolder>> = {}) {
  return render(
    <DocListFolder folder={folder} documents={[]} isActive={false} onSelect={vi.fn()} onDocClick={vi.fn()} {...over} />,
  );
}

async function openMenu() {
  await userEvent.click(screen.getByRole("button", { name: "Folder actions" }));
}

beforeEach(() => {
  mockFetch.mockReset();
  vi.mocked(notifyDocumentsChanged).mockReset();
});

describe("searchable folders", () => {
  it("offers nothing when the backend has no knowledge bases", async () => {
    renderFolder(llm, { searchAvailable: false });
    await openMenu();
    expect(screen.queryByRole("menuitem", { name: "Make searchable" })).toBeNull();
    expect(screen.queryByTestId("folder-search-badge")).toBeNull();
  });

  it("Make searchable posts the folder and shows a pending badge", async () => {
    let resolve!: (r: Response) => void;
    mockFetch.mockReturnValue(new Promise((r) => (resolve = r)));
    renderFolder(llm, { searchAvailable: true });
    await openMenu();
    await userEvent.click(screen.getByRole("menuitem", { name: "Make searchable" }));
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/knowledge-bases", expect.objectContaining({ method: "POST", body: JSON.stringify({ folderId: "f-llm" }) }));
    expect(screen.getByTestId("folder-search-badge")).toHaveTextContent("Making searchable…");
    resolve(res(201, { kbId: "llm-abc123" }));
    await waitFor(() => expect(notifyDocumentsChanged).toHaveBeenCalled());
  });

  it("shows the server's message when it fails (never silent)", async () => {
    mockFetch.mockResolvedValue(res(409, { detail: { code: "FOLDER_ALREADY_SEARCHABLE", message: "this folder is already searchable" } }));
    renderFolder(llm, { searchAvailable: true });
    await openMenu();
    await userEvent.click(screen.getByRole("menuitem", { name: "Make searchable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("this folder is already searchable");
  });

  it("a searchable folder shows its badge and Stop searching asks first", async () => {
    mockFetch.mockResolvedValue(res(200, { deleted: "llm-abc123" }));
    renderFolder({ ...llm, knowledgeBaseId: "llm-abc123" }, { searchAvailable: true, knowledgeBase: kb });
    expect(screen.getByTestId("folder-search-badge")).toHaveTextContent("Searchable · 11");
    await openMenu();
    expect(screen.queryByRole("menuitem", { name: "Make searchable" })).toBeNull();
    await userEvent.click(screen.getByRole("menuitem", { name: "Stop searching" }));
    expect(mockFetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Stop searching" }));
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/knowledge-bases/llm-abc123", { method: "DELETE" });
  });

  it("badge text follows indexing progress and failures", () => {
    expect(searchBadge({ ...kb, state: "queued" }).text).toBe("Indexing…");
    expect(searchBadge({ ...kb, state: "indexing", done: 3 }).text).toBe("Indexing 3/11");
    expect(searchBadge({ ...kb, failed: 1 }).title).toContain("1 could not be indexed");
    expect(searchBadge(undefined).text).toBe("Searchable");
  });
});
