import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../BlocksRenderer", () => ({
  BlocksRenderer: ({ blocks }: { blocks: unknown[] }) => (
    <div data-testid="blocks-renderer">blocks:{blocks.length}</div>
  ),
}));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { DocumentViewer } from "../DocumentViewer";
import type { DocumentDetail } from "@/hooks/useDocument";

const fetchMock = vi.mocked(fetchWithAuth);

beforeEach(() => {
  fetchMock.mockReset();
});

// jsdom doesn't ship a URL.createObjectURL implementation; stub one.
if (typeof (globalThis as { URL: typeof URL }).URL.createObjectURL !== "function") {
  let counter = 0;
  (globalThis as { URL: typeof URL }).URL.createObjectURL = vi.fn(() => `blob:mock-${++counter}`);
  (globalThis as { URL: typeof URL }).URL.revokeObjectURL = vi.fn();
}

function makeDoc(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: "doc-123",
    originalFilename: "test.pdf",
    sourceFormat: "pdf",
    parseStatus: "parsed",
    parseError: null,
    blockCount: 100,
    blocks: [{ type: "text", text: "Hello" } as never],
    sourceUrl: "gs://bucket/path/test.pdf",
    folderId: null,
    parsedAt: null,
    ...overrides,
  } as DocumentDetail;
}

describe("DocumentViewer", () => {
  it("fetches the PDF bytes via fetchWithAuth and renders an iframe pointed at the Blob URL", async () => {
    // Passing a Blob to the Response constructor triggers a Node/undici
    // mismatch in jsdom ("object.stream is not a function" inside
    // Response.blob()) — feed the bytes in directly via Uint8Array and
    // set Content-Type via headers, which Response handles natively.
    fetchMock.mockResolvedValueOnce(
      new Response(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );
    render(<DocumentViewer doc={makeDoc({ sourceFormat: "pdf", id: "abc-123" })} />);

    // Loading state while fetch resolves
    expect(screen.getByText(/Loading preview/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTitle("test.pdf")).toBeInTheDocument();
    });
    const iframe = screen.getByTitle("test.pdf") as HTMLIFrameElement;
    // The src must NOT contain the API path (that path now flows via JS fetch,
    // not iframe load — otherwise the iframe would hit the backend without
    // Authorization and 401).
    expect(iframe.src).not.toContain("/api/proxy/api/documents/");
    expect(iframe.src.startsWith("blob:")).toBe(true);
    // Confirm the auth fetch path was used.
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toContain("/api/proxy/api/documents/abc-123/preview");
    // No block renderer for PDFs
    expect(screen.queryByTestId("blocks-renderer")).toBeNull();
  });

  it("URL-encodes the docId in the underlying fetch URL", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(new Uint8Array([0x25]), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );
    render(<DocumentViewer doc={makeDoc({ sourceFormat: "pdf", id: "a b/c?d" })} />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledOnce();
    });
    expect(fetchMock.mock.calls[0][0]).toContain("a%20b%2Fc%3Fd");
    expect(fetchMock.mock.calls[0][0]).toContain("/preview");
  });

  it("shows a user-friendly error when the preview fetch returns non-200", async () => {
    fetchMock.mockResolvedValueOnce(new Response("", { status: 403 }));
    render(<DocumentViewer doc={makeDoc({ sourceFormat: "pdf" })} />);
    await waitFor(() => {
      expect(screen.getByText(/Preview unavailable \(HTTP 403\)/i)).toBeInTheDocument();
    });
  });

  it("renders BlocksRenderer for non-PDF formats (e.g. docx) so structure is preserved", () => {
    render(<DocumentViewer doc={makeDoc({ sourceFormat: "docx" })} />);
    expect(screen.getByTestId("blocks-renderer")).toBeInTheDocument();
    expect(screen.queryByTitle("test.pdf")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
