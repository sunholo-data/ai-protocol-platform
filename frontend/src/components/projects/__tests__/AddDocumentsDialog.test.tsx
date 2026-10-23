import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/components/doc-browser/GCSFileBrowser", () => ({
  GCSFileBrowser: ({ onPick }: { onPick: (b: string, o: string, l: string) => void }) => (
    <button type="button" onClick={() => onPick("tenant-bucket", "PPAs/LEAP.pdf", "LEAP.pdf")}>
      LEAP.pdf
    </button>
  ),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { AddDocumentsDialog } from "../AddDocumentsDialog";

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

const recent = (docs: Array<{ id: string; originalFilename: string }>) =>
  res(200, {
    documents: docs.map((d) => ({ ...d, sourceFormat: "pdf", parseStatus: "parsed", lastUsedAt: "2026-10-20T09:00:00Z", lastSessionId: null, lastSessionTitle: null })),
  });

const RECENT = "GET /api/proxy/api/documents/recent?limit=50";
const FOLDERS = "GET /api/proxy/api/folders";

function renderDialog(props: Partial<React.ComponentProps<typeof AddDocumentsDialog>> = {}) {
  const onOpenChange = vi.fn();
  const onAdded = vi.fn();
  render(
    <AddDocumentsDialog
      open
      onOpenChange={onOpenChange}
      projectId="p1"
      projectName="Iberia portfolio review"
      existingDocIds={new Set(["d-in"])}
      onAdded={onAdded}
      {...props}
    />,
  );
  return { onOpenChange, onAdded };
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("Add documents — Your documents", () => {
  it("lists recent documents; ones already in the project are ticked and disabled", async () => {
    route({
      [RECENT]: () => recent([{ id: "d-in", originalFilename: "EDP.pdf" }, { id: "d2", originalFilename: "LEAP.pdf" }]),
      [FOLDERS]: () => res(200, { folders: [] }),
    });
    renderDialog();
    const already = await screen.findByRole("checkbox", { name: /EDP\.pdf/ });
    expect(already).toBeChecked();
    expect(already).toBeDisabled();
    expect(screen.getByText("In this project")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /LEAP\.pdf/ })).not.toBeChecked();
  });

  it("a document tagged into this project beyond the home's first page is still shown as in it", async () => {
    route({
      [RECENT]: () =>
        res(200, {
          documents: [{ id: "d9", originalFilename: "Annex.pdf", sourceFormat: "pdf", parseStatus: "parsed", lastUsedAt: "2026-10-20T09:00:00Z", lastSessionId: null, lastSessionTitle: null, projectIds: ["p1"] }],
        }),
      [FOLDERS]: () => res(200, { folders: [] }),
    });
    renderDialog({ existingDocIds: new Set() });
    const box = await screen.findByRole("checkbox", { name: /Annex\.pdf/ });
    expect(box).toBeChecked();
    expect(box).toBeDisabled();
  });

  it("an empty library says how to get documents in", async () => {
    route({ [RECENT]: () => res(200, { documents: [] }), [FOLDERS]: () => res(200, { folders: [] }) });
    renderDialog();
    expect(await screen.findByText(/No documents yet — upload one/)).toBeInTheDocument();
  });

  it("a load failure is visible, with Retry", async () => {
    let calls = 0;
    route({
      [RECENT]: () => (++calls === 1 ? res(500) : recent([{ id: "d2", originalFilename: "LEAP.pdf" }])),
      [FOLDERS]: () => res(200, { folders: [] }),
    });
    renderDialog();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load your documents.");
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("checkbox", { name: /LEAP\.pdf/ })).toBeInTheDocument();
  });

  it("search narrows the list and says when nothing matches", async () => {
    const user = userEvent.setup();
    route({
      [RECENT]: () => recent([{ id: "d1", originalFilename: "EDP.pdf" }, { id: "d2", originalFilename: "LEAP.pdf" }]),
      [FOLDERS]: () => res(200, { folders: [] }),
    });
    renderDialog();
    await screen.findByRole("checkbox", { name: /LEAP\.pdf/ });
    await user.type(screen.getByRole("searchbox", { name: "Search your documents" }), "leap");
    expect(screen.queryByRole("checkbox", { name: /EDP\.pdf/ })).toBeNull();
    await user.type(screen.getByRole("searchbox", { name: "Search your documents" }), "zzz");
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
  });

  it("the folder filter lists that folder's documents", async () => {
    const user = userEvent.setup();
    route({
      [RECENT]: () => recent([]),
      [FOLDERS]: () => res(200, { folders: [{ id: "f1", name: "PPAs 2025" }] }),
      "GET /api/proxy/api/folders/f1/documents": () => res(200, { documents: [{ id: "d7", originalFilename: "Old PPA.pdf" }] }),
    });
    renderDialog();
    await user.selectOptions(await screen.findByRole("combobox", { name: "Folder" }), await screen.findByRole("option", { name: "PPAs 2025" }));
    expect(await screen.findByRole("checkbox", { name: /Old PPA\.pdf/ })).toBeInTheDocument();
  });

  it("adds the selection — 'Adding 2…' — then closes and reports the count", async () => {
    const user = userEvent.setup();
    let resolve!: (r: Response) => void;
    route({
      [RECENT]: () => recent([{ id: "d1", originalFilename: "EDP.pdf" }, { id: "d2", originalFilename: "LEAP.pdf" }]),
      [FOLDERS]: () => res(200, { folders: [] }),
      "POST /api/proxy/api/projects/p1/documents": () => new Promise<Response>((r) => (resolve = r)),
    });
    const { onOpenChange, onAdded } = renderDialog();
    await user.click(await screen.findByRole("checkbox", { name: /EDP\.pdf/ }));
    await user.click(screen.getByRole("checkbox", { name: /LEAP\.pdf/ }));
    await user.click(screen.getByRole("button", { name: "Add 2 documents" }));
    expect(screen.getByRole("button", { name: "Adding 2…" })).toBeDisabled();
    await act(async () => resolve(res(200, { added: ["d1", "d2"], already: [], failed: [] })));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onAdded).toHaveBeenCalledWith(2);
  });

  it("names the documents that couldn't be added, by filename, and keeps them selected", async () => {
    const user = userEvent.setup();
    route({
      [RECENT]: () => recent([{ id: "d1", originalFilename: "EDP.pdf" }, { id: "d2", originalFilename: "LEAP.pdf" }]),
      [FOLDERS]: () => res(200, { folders: [] }),
      "POST /api/proxy/api/projects/p1/documents": () => res(200, { added: ["d1"], already: [], failed: [{ id: "d2" }] }),
    });
    const { onOpenChange } = renderDialog();
    await user.click(await screen.findByRole("checkbox", { name: /EDP\.pdf/ }));
    await user.click(screen.getByRole("checkbox", { name: /LEAP\.pdf/ }));
    await user.click(screen.getByRole("button", { name: "Add 2 documents" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't add: LEAP.pdf.");
    expect(screen.getByRole("button", { name: "Add 1 document" })).toBeEnabled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});

describe("Add documents — Shared library", () => {
  it("imports the pick into the user's library, then tags it", async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    route({
      [RECENT]: () => recent([]),
      [FOLDERS]: () => res(200, { folders: [] }),
      "POST /api/proxy/api/documents/import-by-reference": (init) => {
        calls.push(String(init?.body));
        return res(200, { docId: "d-imported", status: "parsed", originalFilename: "LEAP.pdf" });
      },
      "POST /api/proxy/api/projects/p1/documents": (init) => {
        calls.push(String(init?.body));
        return res(200, { added: ["d-imported"], already: [], failed: [] });
      },
    });
    const { onAdded } = renderDialog({ library: { bucket: "tenant-bucket", rootPath: "PPAs/", label: "Example library", skillId: "s1" } });
    await user.click(screen.getByRole("tab", { name: "Shared library" }));
    await user.click(await screen.findByRole("button", { name: "LEAP.pdf" }));
    expect(await screen.findByText("Added LEAP.pdf")).toBeInTheDocument();
    expect(JSON.parse(calls[0])).toEqual({ bucket: "tenant-bucket", object: "PPAs/LEAP.pdf", skillId: "s1" });
    expect(JSON.parse(calls[1])).toEqual({ doc_ids: ["d-imported"] });
    expect(onAdded).toHaveBeenCalledWith(1);
  });

  it("a failed import is shown, and nothing is tagged", async () => {
    const user = userEvent.setup();
    route({
      [RECENT]: () => recent([]),
      [FOLDERS]: () => res(200, { folders: [] }),
      "POST /api/proxy/api/documents/import-by-reference": () => res(403, { detail: "You can't open that file." }),
    });
    renderDialog({ library: { bucket: "tenant-bucket", skillId: "s1" } });
    await user.click(screen.getByRole("tab", { name: "Shared library" }));
    await user.click(await screen.findByRole("button", { name: "LEAP.pdf" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't bring in LEAP.pdf: You can't open that file.");
  });

  it("without a library the tab says so", async () => {
    const user = userEvent.setup();
    route({ [RECENT]: () => recent([]), [FOLDERS]: () => res(200, { folders: [] }) });
    renderDialog({ library: null });
    await user.click(screen.getByRole("tab", { name: "Shared library" }));
    expect(await screen.findByText(/no shared library for this project/)).toBeInTheDocument();
  });
});
