// v6.34.0 DOC-WORKBENCH — Styled / Text toggle.
//
// The block view carries no styling, because styling lives in the .docx theme
// and styles parts which the block model does not model. So applying a
// letterhead changed the file and nothing on screen — the feature was real and
// invisible (reported on test 2026-09-02).
//
// What is pinned here: the styled view is the default where it can be drawn,
// the block view stays reachable (it is what the AI reads, and the fallback
// when a document defeats the renderer), and neither is offered for a format
// that cannot support it.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentViewer } from "../DocumentViewer";
import type { DocumentDetail } from "@/hooks/useDocument";

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn(async () => ({ ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(8) })),
}));
vi.mock("docx-preview", () => ({ renderAsync: vi.fn(async () => undefined) }));

function doc(over: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: "d1",
    originalFilename: "Offer.docx",
    sourceFormat: "docx",
    parseStatus: "parsed",
    parseError: null,
    sourceUrl: "gs://b/o",
    parsedAt: "2026-09-02T10:00:00Z",
    summary: null,
    blocks: [{ type: "heading", level: 1, text: "Commercial Offer" }],
    ...over,
  } as DocumentDetail;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DocumentViewer — styled/text toggle", () => {
  it("offers both views for a .docx", () => {
    render(<DocumentViewer doc={doc()} />);
    expect(screen.getByRole("button", { name: "Styled" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Text" })).toBeInTheDocument();
  });

  it("shows the styled view by default, so formatting changes are visible", () => {
    render(<DocumentViewer doc={doc()} />);
    expect(screen.getByRole("button", { name: "Styled" })).toHaveAttribute("aria-pressed", "true");
    // The block text is not on screen while styled.
    expect(screen.queryByText("Commercial Offer")).not.toBeInTheDocument();
  });

  it("switches to the block view — what the assistant reads", async () => {
    render(<DocumentViewer doc={doc()} />);
    await userEvent.click(screen.getByRole("button", { name: "Text" }));

    expect(await screen.findByText("Commercial Offer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Text" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers no toggle for a format the renderer cannot draw", () => {
    render(<DocumentViewer doc={doc({ sourceFormat: "csv" })} />);
    expect(screen.queryByRole("button", { name: "Styled" })).not.toBeInTheDocument();
    expect(screen.getByText("Commercial Offer")).toBeInTheDocument();
  });

  it("still renders a docx that produced no blocks — styled is enough", () => {
    render(<DocumentViewer doc={doc({ blocks: [] })} />);
    expect(screen.getByRole("button", { name: "Styled" })).toBeInTheDocument();
    expect(screen.queryByText(/preview unavailable/i)).not.toBeInTheDocument();
  });

  it("says so when a non-docx has nothing to show", () => {
    render(<DocumentViewer doc={doc({ sourceFormat: "csv", blocks: [] })} />);
    expect(screen.getByText(/preview unavailable/i)).toBeInTheDocument();
  });

  it("tells the user the Text view is what the assistant reads", () => {
    render(<DocumentViewer doc={doc()} />);
    expect(screen.getByRole("button", { name: "Text" })).toHaveAttribute(
      "title",
      expect.stringContaining("assistant reads") as unknown as string,
    );
  });
});
