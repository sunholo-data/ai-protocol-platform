import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ProtocolStripe } from "../ProtocolStripe";
import { BRANDING } from "@/lib/branding";

describe("ProtocolStripe", () => {
  it("renders all 6 pillars from BRANDING.demo.pillars", () => {
    render(<ProtocolStripe />);
    for (const pillar of BRANDING.demo.pillars) {
      expect(screen.getByText(pillar.label)).toBeInTheDocument();
      expect(screen.getByText(pillar.tagline)).toBeInTheDocument();
    }
  });

  it("hides the 'See the full stack' link when techHref is empty", () => {
    // Default BRANDING.demo.techHref is "" — link should not render.
    render(<ProtocolStripe />);
    expect(
      screen.queryByText(/see the full stack/i),
    ).not.toBeInTheDocument();
  });

  it("renders 'Built on' section heading", () => {
    render(<ProtocolStripe />);
    expect(screen.getByText(/built on/i)).toBeInTheDocument();
  });
});
