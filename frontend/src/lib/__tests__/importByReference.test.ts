import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the auth helper so we can assert the request shape without going to
// the wire. Must be defined before the SUT import.
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { importByReference, isImportError } from "../importByReference";

const fetchMock = vi.mocked(fetchWithAuth);

beforeEach(() => {
  fetchMock.mockReset();
});

describe("importByReference", () => {
  it("POSTs to /api/proxy/api/documents/import-by-reference with the expected body and returns a ParsedDocument on 200", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          docId: "doc-123",
          status: "parsed",
          originalFilename: "example-A-fixed-pap.pdf",
          blocksCount: 42,
          storagePath: "aitana3/PPAs/longform/example-A-fixed-pap.pdf",
          folderId: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await importByReference(
      "your-project-id-test-test-llmops-bucket",
      "aitana3/PPAs/longform/example-A-fixed-pap.pdf",
      "one-ppa-expert",
    );

    // Wire shape — exactly what the backend expects.
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/proxy/api/documents/import-by-reference");
    expect(init?.method).toBe("POST");
    expect(JSON.parse((init?.body as string) ?? "")).toEqual({
      bucket: "your-project-id-test-test-llmops-bucket",
      object: "aitana3/PPAs/longform/example-A-fixed-pap.pdf",
      skillId: "one-ppa-expert",
    });

    // Response shape — what handleDocClick expects.
    expect(isImportError(result)).toBe(false);
    if (!isImportError(result)) {
      expect(result.doc.id).toBe("doc-123");
      expect(result.doc.originalFilename).toBe("example-A-fixed-pap.pdf");
      expect(result.doc.sourceFormat).toBe("pdf");
      expect(result.doc.parseStatus).toBe("parsed");
      expect(result.doc.blockCount).toBe(42);
    }
  });

  it("mounts the doc with pending_ai_extraction status (does NOT treat as fatal)", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          docId: "doc-pending",
          status: "pending_ai_extraction",
          originalFilename: "scanned.pdf",
          blocksCount: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await importByReference("b", "scanned.pdf", "skill");

    expect(isImportError(result)).toBe(false);
    if (!isImportError(result)) {
      expect(result.doc.parseStatus).toBe("pending_ai_extraction");
      expect(result.doc.id).toBe("doc-pending");
    }
  });

  it("returns an ImportByReferenceError with the backend detail on 422 (parse failure)", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Unsupported format: .xyz" }), {
        status: 422,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await importByReference("b", "weird.xyz", "skill");

    expect(isImportError(result)).toBe(true);
    if (isImportError(result)) {
      expect(result.status).toBe(422);
      expect(result.message).toBe("Unsupported format: .xyz");
    }
  });
});
