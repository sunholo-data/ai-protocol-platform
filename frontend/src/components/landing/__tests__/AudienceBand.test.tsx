import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { AudienceBand } from "../AudienceBand";
import { BRANDING } from "@/lib/branding";

describe("AudienceBand", () => {
  it("renders the section heading", () => {
    render(<AudienceBand />);
    expect(
      screen.getByText(/built for the people who read the contract/i),
    ).toBeInTheDocument();
  });

  it("renders all three audience roles", () => {
    render(<AudienceBand />);
    expect(screen.getByText(/technical counsel/i)).toBeInTheDocument();
    expect(screen.getByText(/quants & structurers/i)).toBeInTheDocument();
    expect(screen.getByText(/ai & legal engineers/i)).toBeInTheDocument();
  });

  it("interpolates the product name from BRANDING into the engineer column", () => {
    render(<AudienceBand />);
    expect(
      screen.getByText(new RegExp(`Extend ${BRANDING.appName}`, "i")),
    ).toBeInTheDocument();
  });
});
