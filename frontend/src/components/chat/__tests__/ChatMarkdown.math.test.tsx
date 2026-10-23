import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatMarkdown } from "../ChatMarkdown";

const navigateToBlock = vi.fn();

describe("chat LaTeX rendering", () => {
  it("typesets the reported deck spacing, areas and approximations", () => {
    const content = String.raw`Example floor spacing: $6.5\text{ m}$.

Example area: $250,000\text{ m}^2$ ($\approx 25\text{ hectares}$).
Example ceiling height: $\approx 5.5\text{ m}$.`;
    const { container } = render(<ChatMarkdown content={content} navigateToBlock={navigateToBlock} />);
    expect(container.querySelectorAll(".katex")).toHaveLength(4);
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.querySelectorAll("math")).toHaveLength(4);
    const visible = Array.from(container.querySelectorAll(".katex-html")).map((node) => node.textContent).join(" ");
    expect(visible).toContain("6.5");
    expect(visible).toContain("hectares");
    expect(visible).not.toContain("\\text");
    expect(visible).not.toContain("\\approx");
  });

  it("renders block equations, fractions and superscripts", () => {
    const { container } = render(<ChatMarkdown content={String.raw`Before.

$$
E = \frac{1}{2} m v^2
$$

After.`} navigateToBlock={navigateToBlock} />);
    expect(container.querySelector(".katex-display .katex")).not.toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.textContent).toContain("After.");
  });

  it("leaves source examples and escaped currency as ordinary Markdown", () => {
    const content = [
      "The cost is \\$5 or \\$10. The literal formula is `$x^2$`.",
      "", "```text", "$8.5\\text{ m}$", "```",
    ].join("\n");
    const { container } = render(<ChatMarkdown content={content} navigateToBlock={navigateToBlock} />);
    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$5 or $10");
    expect(container.querySelector("code")?.textContent).toBe("$x^2$");
  });

  it("survives incomplete or malformed math during streaming", () => {
    const { container, rerender } = render(<ChatMarkdown content={String.raw`Partial $\frac{1}`} navigateToBlock={navigateToBlock} />);
    expect(container.textContent).toContain("Partial");
    rerender(<ChatMarkdown content={String.raw`Partial $\frac{1}{2}$`} navigateToBlock={navigateToBlock} />);
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector(".katex-error")).toBeNull();
    rerender(<ChatMarkdown content="Malformed $x^$ then ordinary text." navigateToBlock={navigateToBlock} />);
    expect(container.textContent).toContain("ordinary text.");
    expect((container.querySelector(".katex-error") as HTMLElement).style.color).toBe("inherit");
  });

  it("does not enable unsafe HTML or trusted LaTeX commands", () => {
    const { container } = render(<ChatMarkdown content={String.raw`<script>alert(1)</script>

$\href{javascript:alert(1)}{click}$`} navigateToBlock={navigateToBlock} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
  });
});
