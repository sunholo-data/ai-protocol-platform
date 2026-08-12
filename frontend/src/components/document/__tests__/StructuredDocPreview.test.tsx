import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StructuredDocPreview } from "../StructuredDocPreview";
import type { Block } from "@/components/document/BlocksRenderer";

describe("StructuredDocPreview", () => {
  it("renders headings, paragraphs, tables, and lists from blocks", () => {
    const blocks: Block[] = [
      { type: "heading", level: 1, text: "Power Purchase Agreement" },
      { type: "paragraph", text: "This agreement is entered into between the parties." },
      { type: "list", items: ["Clause one", "Clause two"] },
      { type: "table", headers: [{ text: "Term" }, { text: "Value" }], rows: [{ cells: [{ text: "Price" }, { text: "€45" }] }] },
    ];
    render(<StructuredDocPreview blocks={blocks} />);
    expect(screen.getByText("Power Purchase Agreement")).toBeInTheDocument();
    expect(screen.getByText(/entered into between the parties/i)).toBeInTheDocument();
    expect(screen.getByText("Clause one")).toBeInTheDocument();
    expect(screen.getByText("Term")).toBeInTheDocument();
    expect(screen.getByText("€45")).toBeInTheDocument();
  });

  it("caps the number of blocks shown", () => {
    const blocks: Block[] = Array.from({ length: 40 }, (_, i) => ({ type: "paragraph", text: `para ${i}` }));
    render(<StructuredDocPreview blocks={blocks} limit={5} />);
    expect(screen.getByText("para 0")).toBeInTheDocument();
    expect(screen.queryByText("para 20")).toBeNull();
  });
});
