import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DocumentThumbnail, __clearThumbnailCache } from "../DocumentThumbnail";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
const mockFetch = vi.mocked(fetchWithAuth);

function pngResponse(): Response {
  return {
    ok: true,
    blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
  } as unknown as Response;
}

beforeEach(() => {
  __clearThumbnailCache();
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(pngResponse());
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

describe("DocumentThumbnail", () => {
  it("renders an <img> from the AUTHENTICATED bucket thumbnail route", async () => {
    render(<DocumentThumbnail source={{ kind: "bucket", bucket: "one-ppa", object: "ppa/a.pdf" }} alt="A" />);
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/proxy/api/buckets/one-ppa/thumbnail?object=ppa%2Fa.pdf"),
      ),
    );
    const img = await screen.findByAltText("A");
    expect(img.tagName).toBe("IMG");
    expect(img.getAttribute("src")).toBe("blob:mock");
  });

  it("uses the document thumbnail route for a doc source", async () => {
    render(<DocumentThumbnail source={{ kind: "doc", docId: "doc-9" }} alt="D" />);
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("/api/proxy/api/documents/doc-9/thumbnail")),
    );
    expect(await screen.findByAltText("D")).toBeInTheDocument();
  });

  it("renders an image source too (not just PDFs)", async () => {
    render(<DocumentThumbnail source={{ kind: "bucket", bucket: "b", object: "logo.png" }} alt="img" />);
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("thumbnail?object=logo.png")),
    );
    expect(await screen.findByAltText("img")).toBeInTheDocument();
  });

  it("falls back to an icon (no <img>) when the thumbnail is forbidden", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<DocumentThumbnail source={{ kind: "doc", docId: "secret" }} alt="X" />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(screen.queryByAltText("X")).toBeNull();
  });

  it("does NOT fetch for a non-thumbnailable bucket object (.txt)", () => {
    render(<DocumentThumbnail source={{ kind: "bucket", bucket: "b", object: "notes.txt" }} />);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("caches across mounts — re-rendering the same source does not refetch", async () => {
    const src = { kind: "doc", docId: "cache-me" } as const;
    const { unmount } = render(<DocumentThumbnail source={src} alt="C" />);
    await screen.findByAltText("C");
    expect(mockFetch).toHaveBeenCalledTimes(1);
    unmount();
    // Re-mount (e.g. re-hovering the same doc) → served from cache, no refetch.
    render(<DocumentThumbnail source={src} alt="C" />);
    expect(await screen.findByAltText("C")).toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});
