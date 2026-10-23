import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ParsedDocument } from "@/hooks/useDocBrowser";
import { DocListItem } from "../DocListItem";
import { __clearThumbnailCache } from "@/components/document/DocumentThumbnail";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
const mockFetch = vi.mocked(fetchWithAuth);

// The non-image preview path loads the record via useDocument; stub it so the
// hover tests only see the thumbnail route.
vi.mock("@/hooks/useDocument", () => ({
  useDocument: vi.fn(() => ({ doc: null, isLoading: true, error: null, retry: vi.fn() })),
}));
import { useDocument } from "@/hooks/useDocument";
const mockUseDocument = vi.mocked(useDocument);

beforeEach(() => {
  __clearThumbnailCache();
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({
    ok: true,
    blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
  } as unknown as Response);
  mockUseDocument.mockClear();
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

function thumbCalls(): string[] {
  return mockFetch.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/thumbnail"));
}

function makeDoc(overrides: Partial<ParsedDocument> = {}): ParsedDocument {
  return {
    id: "doc1",
    originalFilename: "report.docx",
    sourceFormat: "docx",
    parseStatus: "parsed",
    parseError: null,
    folderId: "folder1",
    userId: "user1",
    blockCount: 12,
    hasA2ui: true,
    ...overrides,
  };
}

describe("DocListItem", () => {
  it("renders filename and format badge", () => {
    render(<DocListItem doc={makeDoc()} onClick={vi.fn()} />);
    expect(screen.getByText("report.docx")).toBeInTheDocument();
    expect(screen.getByText("docx")).toBeInTheDocument();
  });

  it("shows green dot for parsed status", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "parsed" })} onClick={vi.fn()} />);
    const dot = screen.getByLabelText("Parsed");
    expect(dot.className).toContain("bg-emerald-500");
  });

  it("shows amber pulsing dot for pending status", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "pending" })} onClick={vi.fn()} />);
    const dot = screen.getByLabelText("Pending");
    expect(dot.className).toContain("bg-amber-400");
    expect(dot.className).toContain("animate-pulse");
  });

  it("shows red dot for failed status", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "failed", parseError: null })} onClick={vi.fn()} />);
    const dot = screen.getByLabelText("Failed");
    expect(dot.className).toContain("bg-destructive");
  });

  it("shows error message text when parse failed with error", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "failed", parseError: "API error: 500" })} onClick={vi.fn()} />);
    expect(screen.getByText("API error: 500")).toBeInTheDocument();
  });

  it("shows fallback error text when parse failed with no error message", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "failed", parseError: null })} onClick={vi.fn()} />);
    expect(screen.getByText("Parse failed — content unavailable")).toBeInTheDocument();
  });

  it("does not show retry banner for parsed doc with blocks but no a2uiComponents", () => {
    // Regression: a2uiComponents is an optional render layer written by a
    // separate pipeline. Parsed docs with blockCount > 0 have content even
    // when hasA2ui is false — they should not show the retry banner.
    render(<DocListItem doc={makeDoc({ parseStatus: "parsed", blockCount: 12, hasA2ui: false })} onClick={vi.fn()} />);
    expect(screen.queryByText("No content — re-parse to load")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("shows retry banner for parsed doc with no blocks", () => {
    render(<DocListItem doc={makeDoc({ parseStatus: "parsed", blockCount: 0, hasA2ui: false })} onClick={vi.fn()} />);
    expect(screen.getByText("No content — re-parse to load")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("calls onClick with the doc when clicked", async () => {
    const onClick = vi.fn();
    const doc = makeDoc();
    render(<DocListItem doc={doc} onClick={onClick} />);
    // Row is role=button; the inner delete/reparse <button>s share that role.
    // Click the row by walking up from the filename text.
    const row = screen.getByText("report.docx").closest("[role=\"button\"]");
    expect(row).not.toBeNull();
    await userEvent.click(row!);
    expect(onClick).toHaveBeenCalledWith(doc);
  });
});

describe("DocListItem — hover preview", () => {
  it("fetches nothing until the row is hovered, then the owner-checked doc thumbnail route (pdf)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<DocListItem doc={makeDoc({ id: "p1", originalFilename: "a.pdf", sourceFormat: "pdf" })} onClick={vi.fn()} />);
      expect(thumbCalls()).toEqual([]);
      const trigger = screen.getByText("a.pdf").closest("[role=button]") as HTMLElement;
      fireEvent.pointerMove(trigger, { pointerType: "mouse" });
      // Still nothing inside the hover-intent delay.
      expect(thumbCalls()).toEqual([]);
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      await waitFor(() => expect(thumbCalls()).toEqual(["/api/proxy/api/documents/p1/thumbnail?width=400"]));
      expect(mockUseDocument).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("never calls the thumbnail route for a format the backend can't render (docx)", async () => {
    render(<DocListItem doc={makeDoc({ id: "w1", originalFilename: "a.docx", sourceFormat: "docx" })} onClick={vi.fn()} />);
    await userEvent.tab(); // Radix opens on focus without the hover delay
    await waitFor(() => expect(mockUseDocument).toHaveBeenCalledWith("w1"));
    expect(thumbCalls()).toEqual([]);
  });

  it("shows 'Preview unavailable' when the thumbnail fails", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 502 } as unknown as Response);
    render(<DocListItem doc={makeDoc({ id: "bad1", originalFilename: "b.png", sourceFormat: "png" })} onClick={vi.fn()} />);
    await userEvent.tab();
    expect((await screen.findAllByText("Preview unavailable")).length).toBeGreaterThan(0);
    expect(thumbCalls()).toEqual(["/api/proxy/api/documents/bad1/thumbnail?width=400"]);
  });
});
