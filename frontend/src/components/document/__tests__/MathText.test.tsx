// v6.34.0 DOC-WORKBENCH — formulae in the Text view.
//
// The block view printed the parsed text verbatim, so a document with maths
// showed its raw source. The risk in fixing that is the opposite error: a
// commercial document is full of PRICES, and "$1.2M" must not be mistaken for
// an equation. Both directions are tested.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MathText } from "../MathText";

describe("MathText", () => {
  it("leaves ordinary prose exactly as it was", () => {
    render(<MathText text="The term is ten years." />);
    expect(screen.getByText("The term is ten years.")).toBeInTheDocument();
  });

  it("typesets an inline formula", () => {
    const { container } = render(<MathText text="Given $E = mc^2$ we conclude." />);
    expect(container.querySelector(".katex")).toBeTruthy();
  });

  it("typesets a display formula", () => {
    const { container } = render(<MathText text="$$\\frac{a}{b}$$" />);
    expect(container.querySelector(".katex")).toBeTruthy();
  });

  it("keeps the surrounding prose around a formula", () => {
    const { container } = render(<MathText text="Given $E = mc^2$ we conclude." />);
    expect(container.textContent).toContain("Given");
    expect(container.textContent).toContain("we conclude.");
  });

  it("does NOT treat a price as maths", () => {
    const { container } = render(<MathText text="It costs $1.2M in total." />);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toBe("It costs $1.2M in total.");
  });

  it("does NOT treat a price range as maths", () => {
    const { container } = render(<MathText text="Between $5 and $10 per MWh." />);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toBe("Between $5 and $10 per MWh.");
  });

  it("leaves a lone dollar sign alone", () => {
    const { container } = render(<MathText text="Costs rose by $ per unit." />);
    expect(container.textContent).toBe("Costs rose by $ per unit.");
  });

  it("falls back to the source when a formula will not parse", () => {
    // Unreadable maths beats absent maths.
    const { container } = render(<MathText text="$\\begin{unclosed}=$" />);
    expect(container.textContent).toContain("\\begin{unclosed}");
  });

  it("handles text with no dollar signs without touching it", () => {
    const { container } = render(<MathText text="No maths here at all" />);
    expect(container.textContent).toBe("No maths here at all");
  });

  it("renders an empty string safely", () => {
    const { container } = render(<MathText text="" />);
    expect(container.textContent).toBe("");
  });
});
