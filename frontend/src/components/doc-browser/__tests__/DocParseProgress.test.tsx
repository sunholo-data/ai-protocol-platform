import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DocParseProgress } from "../DocParseProgress";

describe("DocParseProgress", () => {
  it("shows progress when parsedCount < docCount", () => {
    render(<DocParseProgress parsedCount={3} failedCount={0} docCount={10} />);
    expect(screen.getByText(/parsing:.*3.*\/.*10.*complete/i)).toBeInTheDocument();
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("is hidden when all docs are parsed", () => {
    const { container } = render(<DocParseProgress parsedCount={10} failedCount={0} docCount={10} />);
    expect(container.firstChild).toBeNull();
  });

  it("is hidden when docCount is 0", () => {
    const { container } = render(<DocParseProgress parsedCount={0} failedCount={0} docCount={0} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows failed count when some docs failed", () => {
    render(<DocParseProgress parsedCount={2} failedCount={2} docCount={5} />);
    expect(screen.getByText(/2 files failed to parse/i)).toBeInTheDocument();
  });

  it("shows singular failed text for one failure", () => {
    render(<DocParseProgress parsedCount={3} failedCount={1} docCount={5} />);
    expect(screen.getByText(/1 file failed to parse/i)).toBeInTheDocument();
  });

  it("shows only failure message when all remaining docs failed (no progress bar)", () => {
    render(<DocParseProgress parsedCount={2} failedCount={3} docCount={5} />);
    expect(screen.getByText(/3 files failed to parse/i)).toBeInTheDocument();
    expect(screen.queryByText(/parsing:/i)).not.toBeInTheDocument();
  });
});
