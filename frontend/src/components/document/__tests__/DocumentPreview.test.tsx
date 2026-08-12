import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DocumentPreview } from "../DocumentPreview";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
const mockFetch = vi.mocked(fetchWithAuth);

// The doc-source path resolves format + blocks via useDocument; mock it.
vi.mock("@/hooks/useDocument", () => ({ useDocument: vi.fn() }));
import { useDocument } from "@/hooks/useDocument";
const mockUseDocument = vi.mocked(useDocument);

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({
    ok: true,
    blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
  } as unknown as Response);
  mockUseDocument.mockReturnValue({ doc: null, isLoading: false, error: null });
  if (!("createObjectURL" in URL)) {
    // @ts-expect-error test shim
    URL.createObjectURL = vi.fn(() => "blob:mock");
    // @ts-expect-error test shim
    URL.revokeObjectURL = vi.fn();
  } else {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  }
});

describe("DocumentPreview", () => {
  it("renders the trigger child", () => {
    render(
      <DocumentPreview source={{ kind: "bucket", bucket: "b", object: "a.pdf" }} label="a.pdf">
        <button>Open a.pdf</button>
      </DocumentPreview>,
    );
    expect(screen.getByRole("button", { name: "Open a.pdf" })).toBeInTheDocument();
  });

  it("is lazy — no preview is fetched until it opens", () => {
    render(
      <DocumentPreview source={{ kind: "bucket", bucket: "b", object: "a.pdf" }} label="a.pdf">
        <button>Open a.pdf</button>
      </DocumentPreview>,
    );
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("loads the image thumbnail for a bucket source once opened", async () => {
    render(
      <DocumentPreview source={{ kind: "bucket", bucket: "one-ppa", object: "ppa/a.pdf" }} label="a.pdf">
        <button>Open</button>
      </DocumentPreview>,
    );
    await userEvent.tab(); // Radix Tooltip opens on focus (no hover delay)
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("/api/proxy/api/buckets/one-ppa/thumbnail")),
    );
  });

  it("shows a STRUCTURED block preview for a docparse'd doc (docx) — no image fetch", async () => {
    mockUseDocument.mockReturnValue({
      doc: {
        id: "d1",
        originalFilename: "contract.docx",
        sourceFormat: "docx",
        parseStatus: "parsed",
        parseError: null,
        sourceUrl: "gs://b/contract.docx",
        parsedAt: null,
        summary: null,
        blocks: [{ type: "heading", level: 1, text: "Master Services Agreement" }],
      },
      isLoading: false,
      error: null,
    });
    render(
      <DocumentPreview source={{ kind: "doc", docId: "d1" }} label="contract.docx">
        <button>Open docx</button>
      </DocumentPreview>,
    );
    await userEvent.tab();
    // Radix Tooltip can render the content text more than once — assert presence.
    expect((await screen.findAllByText("Master Services Agreement")).length).toBeGreaterThan(0);
    // A docx renders from blocks, not the image thumbnail route.
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
