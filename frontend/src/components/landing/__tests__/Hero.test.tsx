import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Hero } from "../Hero";
import { BRANDING } from "@/lib/branding";

describe("Hero", () => {
  it("renders eyebrow + headline lines + body from BRANDING.demo", () => {
    render(<Hero />);
    expect(screen.getByText(BRANDING.demo.heroEyebrow)).toBeInTheDocument();
    expect(screen.getByText(BRANDING.demo.heroLineA)).toBeInTheDocument();
    expect(screen.getByText(BRANDING.demo.heroLineB)).toBeInTheDocument();
    expect(screen.getByText(BRANDING.demo.heroBody)).toBeInTheDocument();
  });

  it("renders both CTAs with correct hrefs from BRANDING.demo", () => {
    render(<Hero />);
    const primary = screen.getByRole("link", {
      name: new RegExp(BRANDING.demo.ctaPrimary, "i"),
    });
    expect(primary).toHaveAttribute("href", BRANDING.demo.chatHref);

    const secondary = screen.getByRole("link", {
      name: new RegExp(BRANDING.demo.ctaSecondary, "i"),
    });
    expect(secondary).toHaveAttribute("href", BRANDING.demo.chatHrefSecondary);
  });

  it("renders a right-column visual when the visual prop is provided", () => {
    render(
      <Hero visual={<div data-testid="custom-visual">visual content</div>} />,
    );
    expect(screen.getByTestId("custom-visual")).toBeInTheDocument();
  });

  it("uses single-column layout when no visual prop is provided", () => {
    const { container } = render(<Hero />);
    const layout = container.querySelector("section > div");
    expect(layout?.className).toContain("flex-col");
  });
});
