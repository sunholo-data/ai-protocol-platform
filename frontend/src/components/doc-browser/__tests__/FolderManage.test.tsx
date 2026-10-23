import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Folder, ParsedDocument, useDocBrowser } from "@/hooks/useDocBrowser";
import { DocListFolder } from "../DocListFolder";
import { DocListItem } from "../DocListItem";
import { DocListView } from "../DocListView";
import { DOC_DRAG_TYPE } from "../folderActions";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/lib/documentEvents", () => ({ notifyDocumentsChanged: vi.fn(), subscribeDocumentsChanged: vi.fn() }));
vi.mock("@/components/document/DocumentPreview", () => ({
  DocumentPreview: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
type State = ReturnType<typeof useDocBrowser>;
let hookState: State;
vi.mock("@/hooks/useDocBrowser", () => ({ useDocBrowser: () => hookState }));

import { fetchWithAuth } from "@/lib/apiClient";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
const mockFetch = vi.mocked(fetchWithAuth);
const mockNotify = vi.mocked(notifyDocumentsChanged);

function res(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body ?? {},
  } as unknown as Response;
}

const papers: Folder = { id: "f-papers-uuid", name: "Papers", userId: "u1", docCount: 0, parsedCount: 0 };
const llm: Folder = { id: "f-llm-uuid", name: "llm", userId: "u1", docCount: 11, parsedCount: 11 };
const created: Folder = { id: "f-created", name: "Created", userId: "u1", docCount: 1, parsedCount: 1, system: true };

const doc: ParsedDocument = {
  id: "d-uuid-1",
  originalFilename: "a.pdf",
  sourceFormat: "pdf",
  parseStatus: "parsed",
  parseError: null,
  folderId: llm.id,
  userId: "u1",
  blockCount: 3,
  hasA2ui: false,
};

function renderFolder(folder: Folder, over: Partial<React.ComponentProps<typeof DocListFolder>> = {}) {
  return render(
    <DocListFolder
      folder={folder}
      documents={[]}
      isActive={false}
      onSelect={vi.fn()}
      onDocClick={vi.fn()}
      folders={[papers, llm, created]}
      {...over}
    />,
  );
}

async function openFolderMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Folder actions" }));
}

beforeEach(() => {
  mockFetch.mockReset();
  mockNotify.mockReset();
});

describe("folder rename", () => {
  it("renames on Enter, PATCHes the name and refreshes the lists", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(res(200, { ...llm, name: "LLM papers" }));
    renderFolder(llm);
    await openFolderMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Folder name" });
    await user.clear(input);
    await user.type(input, "LLM papers{Enter}");
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/folders/f-llm-uuid");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ name: "LLM papers" });
    expect(screen.queryByRole("textbox", { name: "Folder name" })).not.toBeInTheDocument();
  });

  it("shows FOLDER_EXISTS visibly and keeps editing", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(
      res(409, { detail: { code: "FOLDER_EXISTS", message: "You already have a folder called 'Papers'." } }),
    );
    renderFolder(llm);
    await openFolderMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Folder name" });
    await user.clear(input);
    await user.type(input, "Papers{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("You already have a folder called 'Papers'.");
    expect(screen.getByRole("textbox", { name: "Folder name" })).toBeInTheDocument();
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it("Esc cancels without calling the server", async () => {
    const user = userEvent.setup();
    renderFolder(llm);
    await openFolderMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Rename" }));
    await user.type(screen.getByRole("textbox", { name: "Folder name" }), "x{Escape}");
    expect(screen.queryByRole("textbox", { name: "Folder name" })).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("offers no rename/delete menu on the assistant's system folder", () => {
    renderFolder(created);
    expect(screen.queryByRole("button", { name: "Folder actions" })).not.toBeInTheDocument();
  });
});

describe("folder delete", () => {
  it("confirms, DELETEs, refreshes and reports the deletion", async () => {
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    mockFetch.mockResolvedValue(res(204));
    renderFolder(papers, { onDeleted });
    await openFolderMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Delete folder ‘Papers’?");
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("f-papers-uuid"));
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/folders/f-papers-uuid", { method: "DELETE" });
    expect(mockNotify).toHaveBeenCalledOnce();
  });

  it("shows FOLDER_NOT_EMPTY's message and keeps the folder", async () => {
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    const message = "'llm' still has 11 documents. Move or delete them first.";
    mockFetch.mockResolvedValue(res(409, { detail: { code: "FOLDER_NOT_EMPTY", message, count: 11 } }));
    renderFolder(llm, { onDeleted });
    await openFolderMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByText("llm")).toBeInTheDocument();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it("selects another folder when the active one is deleted", async () => {
    const user = userEvent.setup();
    const setActiveFolderId = vi.fn();
    hookState = {
      folders: [created, papers, llm],
      activeFolderId: papers.id,
      documents: [],
      searchQuery: "",
      filteredDocuments: [],
      setActiveFolderId,
      setSearchQuery: vi.fn(),
      loading: false,
      error: null,
      retry: vi.fn(),
    };
    mockFetch.mockResolvedValue(res(204));
    render(<DocListView uid="u1" />);
    // Folder rows render in order: created (no menu), papers, llm.
    await user.click(screen.getAllByRole("button", { name: "Folder actions" })[0]);
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(setActiveFolderId).toHaveBeenCalledWith(llm.id));
  });
});

describe("move a document", () => {
  async function openMoveMenu(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Document actions" }));
    expect(await screen.findByText("Move to…")).toBeInTheDocument();
  }

  it("lists the other folders by name and PATCHes the chosen one", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    mockFetch.mockResolvedValue(res(200, { ...doc, folderId: papers.id }));
    render(<DocListItem doc={doc} onClick={onClick} moveTargets={[papers]} />);
    await openMoveMenu(user);
    const target = await screen.findByRole("menuitem", { name: "Move to Papers" });
    expect(target).toHaveTextContent("Papers");
    // Names only — never the raw folder id (rule #9).
    expect(screen.queryByText(/f-papers-uuid/)).not.toBeInTheDocument();
    await user.click(target);
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/documents/d-uuid-1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ folder_id: "f-papers-uuid" });
    expect(onClick).not.toHaveBeenCalled(); // the menu must not open the doc
  });

  it("FILENAME_EXISTS → confirm → Replace retries with replace:true", async () => {
    const user = userEvent.setup();
    const message = "'Papers' already has a document called 'a.pdf'. Replace it?";
    mockFetch
      .mockResolvedValueOnce(res(409, { detail: { code: "FILENAME_EXISTS", message, existingDocId: "d-old" } }))
      .mockResolvedValueOnce(res(200, { ...doc, folderId: papers.id }));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await openMoveMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Move to Papers" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent(message);
    expect(mockNotify).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Replace" }));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toEqual({
      folder_id: "f-papers-uuid",
      replace: true,
    });
  });

  it("FILENAME_EXISTS → Cancel does nothing more", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce(
      res(409, { detail: { code: "FILENAME_EXISTS", message: "Replace it?", existingDocId: "d-old" } }),
    );
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await openMoveMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Move to Papers" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("shows a plain server error string visibly", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(res(500, { detail: "Could not move the stored file: boom" }));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await openMoveMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Move to Papers" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not move the stored file: boom");
  });

  it("is fully keyboard-operable: Enter opens the menu, arrows + Enter move", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    mockFetch.mockResolvedValue(res(200, { ...doc, folderId: papers.id }));
    render(<DocListItem doc={doc} onClick={onClick} moveTargets={[papers]} />);
    screen.getByRole("button", { name: "Document actions" }).focus();
    await user.keyboard("{Enter}");
    await screen.findByRole("menuitem", { name: "Move to Papers" });
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
    expect(onClick).not.toHaveBeenCalled();
  });

  it("dragging a document onto a folder row moves it there", async () => {
    mockFetch.mockResolvedValue(res(200, { ...doc, folderId: papers.id }));
    const { container } = renderFolder(papers);
    const target = container.firstElementChild as HTMLElement;
    const payload = JSON.stringify({ id: doc.id, folderId: llm.id });
    const dataTransfer = {
      types: [DOC_DRAG_TYPE],
      getData: (t: string) => (t === DOC_DRAG_TYPE ? payload : ""),
      dropEffect: "none",
    };
    fireEvent.dragOver(target, { dataTransfer });
    expect(target).toHaveAttribute("data-drop-target", "true");
    fireEvent.drop(target, { dataTransfer });
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/documents/d-uuid-1");
    expect(JSON.parse(String(init?.body))).toEqual({ folder_id: "f-papers-uuid" });
    expect(target).not.toHaveAttribute("data-drop-target");
  });

  it("the document row is draggable and carries its id (and its name, for feedback)", () => {
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    const row = screen.getByText("a.pdf").closest("[draggable]") as HTMLElement;
    const setData = vi.fn();
    const dataTransfer = { setData, effectAllowed: "" };
    fireEvent.dragStart(row, { dataTransfer });
    expect(setData).toHaveBeenCalledWith(
      DOC_DRAG_TYPE,
      JSON.stringify({ id: doc.id, folderId: llm.id, filename: "a.pdf" }),
    );
    // A folder drop moves it; a project drop adds it — both must be allowed.
    expect(dataTransfer.effectAllowed).toBe("copyMove");
  });
});

describe("rename a document", () => {
  async function startRename(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Document actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rename" }));
    return screen.getByRole("textbox", { name: "Document name" });
  }

  it("renames on Enter, PATCHes the filename and shows the name the server kept", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    let resolve!: (r: Response) => void;
    mockFetch.mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    render(<DocListItem doc={doc} onClick={onClick} moveTargets={[papers]} />);
    const input = await startRename(user);
    expect(input).toHaveValue("a.pdf");
    await user.clear(input);
    await user.type(input, "Q3 report{Enter}");
    // Pending: the box locks and says so.
    expect(screen.getByRole("textbox", { name: "Document name" })).toBeDisabled();
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/documents/d-uuid-1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ filename: "Q3 report" });
    // The server keeps the extension; the row shows what it returned.
    resolve(res(200, { ...doc, originalFilename: "Q3 report.pdf" }));
    expect(await screen.findByText("Q3 report.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Document name" })).not.toBeInTheDocument();
    expect(mockNotify).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled(); // renaming must not open the doc
  });

  it("shows a name clash visibly and keeps editing", async () => {
    const user = userEvent.setup();
    const message = "'llm' already has a document called 'b.pdf'. Choose another name.";
    mockFetch.mockResolvedValue(res(409, { detail: { code: "FILENAME_EXISTS", message } }));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    const input = await startRename(user);
    await user.clear(input);
    await user.type(input, "b.pdf{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("textbox", { name: "Document name" })).toBeEnabled();
    expect(mockNotify).not.toHaveBeenCalled();
  });

  it("an empty name is refused in place, without calling the server", async () => {
    const user = userEvent.setup();
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    const input = await startRename(user);
    await user.clear(input);
    await user.type(input, "{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("A document name can't be empty.");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("a network failure is shown, not swallowed", async () => {
    const user = userEvent.setup();
    mockFetch.mockRejectedValue(new Error("offline"));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    const input = await startRename(user);
    await user.type(input, "x{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't rename the document (network error).");
  });

  it("Esc cancels without calling the server", async () => {
    const user = userEvent.setup();
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    const input = await startRename(user);
    await user.type(input, "x{Escape}");
    expect(screen.queryByRole("textbox", { name: "Document name" })).not.toBeInTheDocument();
    expect(screen.getByText("a.pdf")).toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("delete a document", () => {
  it("the bin asks first, naming the document; Delete then DELETEs", async () => {
    const user = userEvent.setup();
    let resolve!: (r: Response) => void;
    mockFetch.mockReturnValue(new Promise<Response>((r) => (resolve = r)));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    expect(mockFetch).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog", { name: "Delete document?" });
    expect(dialog).toHaveTextContent("Delete ‘a.pdf’? This can’t be undone.");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(within(dialog).getByRole("button", { name: "Deleting…" })).toBeDisabled();
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/documents/d-uuid-1", { method: "DELETE" });
    resolve(res(204));
    await waitFor(() => expect(mockNotify).toHaveBeenCalledOnce());
  });

  it("Delete in the ⋯ menu asks the same question", async () => {
    const user = userEvent.setup();
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await user.click(screen.getByRole("button", { name: "Document actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(screen.getByRole("alertdialog", { name: "Delete document?" })).toHaveTextContent("Delete ‘a.pdf’?");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("Cancel does nothing", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<DocListItem doc={doc} onClick={onClick} moveTargets={[papers]} />);
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockNotify).not.toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("a failed delete is shown visibly", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(res(500, { detail: "boom" }));
    render(<DocListItem doc={doc} onClick={vi.fn()} moveTargets={[papers]} />);
    await user.click(screen.getByRole("button", { name: "Delete document" }));
    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(mockNotify).not.toHaveBeenCalled();
  });
});
