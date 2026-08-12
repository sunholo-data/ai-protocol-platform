import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock the useDocument hook so tests don't hit the network
vi.mock("@/hooks/useDocument", () => ({
  useDocument: vi.fn(),
}));

import { useDocument } from "@/hooks/useDocument";
import { DocumentPanel } from "../DocumentPanel";

const _mockUseDocument = useDocument as ReturnType<typeof vi.fn>;

const FIXTURE_DOC = {
  id: "doc-1",
  originalFilename: "quarterly-report.docx",
  sourceFormat: "docx",
  parseStatus: "parsed",
  parseError: null,
  sourceUrl: "gs://bucket/report.docx",
  parsedAt: "2026-04-24T10:00:00Z",
  summary: { totalBlocks: 42, headings: 5, tables: 3, images: 1, changes: 0 },
  blocks: [
    { type: "heading", level: 1, text: "Quarterly Report" },
    { type: "text", text: "Summary paragraph.", style: "Normal" },
    {
      type: "table",
      headers: [{ text: "Region" }, { text: "Revenue" }],
      rows: [
        { cells: [{ text: "EMEA" }, { text: "$1.2M" }] },
        { cells: [{ text: "AMER" }, { text: "$2.4M" }] },
      ],
    },
  ],
};

describe("DocumentPanel", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("renders loading skeleton while fetching", () => {
    _mockUseDocument.mockReturnValue({ doc: null, isLoading: true, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByTestId("doc-panel-loading")).toBeInTheDocument();
  });

  it("shows skeleton with no doc yet (initial subscribe)", () => {
    _mockUseDocument.mockReturnValue({ doc: null, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByTestId("doc-panel-loading")).toBeInTheDocument();
  });

  it("shows 'Parsing document…' caption while parseStatus is pending", () => {
    _mockUseDocument.mockReturnValue({
      doc: { ...FIXTURE_DOC, parseStatus: "pending", blocks: [] },
      isLoading: false,
      error: null,
    });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByTestId("doc-panel-loading")).toBeInTheDocument();
    expect(screen.getByText(/parsing document/i)).toBeInTheDocument();
  });

  it("shows 'Parsing document…' caption while parseStatus is pending_ai_extraction", () => {
    _mockUseDocument.mockReturnValue({
      doc: { ...FIXTURE_DOC, parseStatus: "pending_ai_extraction", blocks: [] },
      isLoading: false,
      error: null,
    });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText(/parsing document/i)).toBeInTheDocument();
  });

  it("renders error state when fetch fails", () => {
    _mockUseDocument.mockReturnValue({ doc: null, isLoading: false, error: "Document preview unavailable." });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
  });

  it("renders parseError on parseStatus=failed", () => {
    _mockUseDocument.mockReturnValue({
      doc: { ...FIXTURE_DOC, parseStatus: "failed", parseError: "AILANG returned 500", blocks: [] },
      isLoading: false,
      error: null,
    });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText("AILANG returned 500")).toBeInTheDocument();
  });

  it("renders generic failed message when parseError is missing", () => {
    _mockUseDocument.mockReturnValue({
      doc: { ...FIXTURE_DOC, parseStatus: "failed", parseError: null, blocks: [] },
      isLoading: false,
      error: null,
    });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText(/parse failed/i)).toBeInTheDocument();
  });

  it("renders filename and format badge from fixture", () => {
    _mockUseDocument.mockReturnValue({ doc: FIXTURE_DOC, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText("quarterly-report.docx")).toBeInTheDocument();
    expect(screen.getByText("docx")).toBeInTheDocument();
  });

  it("renders block summary stats", () => {
    _mockUseDocument.mockReturnValue({ doc: FIXTURE_DOC, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText(/42/)).toBeInTheDocument();
    expect(screen.getByText(/tables/)).toBeInTheDocument();
  });

  it("suppresses zero-count stats", () => {
    _mockUseDocument.mockReturnValue({ doc: FIXTURE_DOC, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    // changes is 0, should not appear
    expect(screen.queryByText(/changes/)).toBeNull();
  });

  it("renders heading text from blocks", () => {
    _mockUseDocument.mockReturnValue({ doc: FIXTURE_DOC, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText("Quarterly Report")).toBeInTheDocument();
  });

  it("renders table content from blocks", () => {
    _mockUseDocument.mockReturnValue({ doc: FIXTURE_DOC, isLoading: false, error: null });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText("Region")).toBeInTheDocument();
    expect(screen.getByText("$1.2M")).toBeInTheDocument();
  });

  it("shows 'no preview content' message when parsed doc has empty blocks", () => {
    _mockUseDocument.mockReturnValue({
      doc: { ...FIXTURE_DOC, blocks: [] },
      isLoading: false,
      error: null,
    });
    render(<DocumentPanel docId="doc-1" />);
    expect(screen.getByText(/no preview content/i)).toBeInTheDocument();
  });
});
