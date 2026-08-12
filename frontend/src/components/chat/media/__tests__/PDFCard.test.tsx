import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PDFCard } from "@/components/chat/media/PDFCard";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

// Mock usePDFInfo hook
vi.mock("@/hooks/usePDFInfo", () => ({
  usePDFInfo: vi.fn(),
}));

import { usePDFInfo } from "@/hooks/usePDFInfo";
const mockUsePDFInfo = vi.mocked(usePDFInfo);

const PDF_URL = "https://storage.googleapis.com/bucket/users/uid/docs/report.pdf";

describe("PDFCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows filename while loading (no page count yet)", () => {
    mockUsePDFInfo.mockReturnValue({ info: null, loading: true });
    render(<PDFCard url={PDF_URL} />);
    expect(screen.getByText("report.pdf")).toBeTruthy();
    // loading spinner "…" is present
    expect(screen.getByText("…")).toBeTruthy();
  });

  it("shows filename and page count badge when info resolves", async () => {
    mockUsePDFInfo.mockReturnValue({
      info: { filename: "Q1-Financial-Report.pdf", pages: 42 },
      loading: false,
    });
    render(<PDFCard url={PDF_URL} />);
    expect(screen.getByText("Q1-Financial-Report.pdf")).toBeTruthy();
    expect(screen.getByText("42p")).toBeTruthy();
  });

  it("shows filename without page count when pages is null (unreadable PDF)", () => {
    mockUsePDFInfo.mockReturnValue({
      info: { filename: "document.pdf", pages: null },
      loading: false,
    });
    render(<PDFCard url={PDF_URL} />);
    expect(screen.getByText("document.pdf")).toBeTruthy();
    expect(screen.queryByText(/p$/)).toBeFalsy();
  });

  it("renders as a link opening in a new tab", () => {
    mockUsePDFInfo.mockReturnValue({ info: null, loading: false });
    render(<PDFCard url={PDF_URL} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe(PDF_URL);
    expect(link.getAttribute("target")).toBe("_blank");
  });
});

describe("ChatMarkdown PDF link rendering", () => {
  const noop = () => {};

  beforeEach(() => {
    mockUsePDFInfo.mockReturnValue({
      info: { filename: "report.pdf", pages: 10 },
      loading: false,
    });
  });

  it("renders a .pdf link as PDFCard (link element present)", () => {
    const md = "[Download report](https://storage.googleapis.com/b/report.pdf)";
    render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    expect(screen.getByRole("link")).toBeTruthy();
    expect(screen.getByText("report.pdf")).toBeTruthy();
  });

  it("renders a non-PDF https link as a plain anchor", () => {
    const md = "[Visit site](https://example.com/page)";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    const anchor = container.querySelector("a");
    expect(anchor?.getAttribute("href")).toBe("https://example.com/page");
    expect(screen.queryByText(/p$/)).toBeFalsy();
  });
});
