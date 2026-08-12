import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ParsedDocument } from "@/hooks/useDocBrowser";
import { DocListItem } from "../DocListItem";

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
