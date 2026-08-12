import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContextBanner } from "../ContextBanner";

describe("ContextBanner", () => {
  it("renders folder name and document count", () => {
    render(
      <ContextBanner context={{ folderName: "Q1 Financial Review", docCount: 14 }} />,
    );
    expect(screen.getByText(/analyzing/i)).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("Q1 Financial Review")).toBeInTheDocument();
  });

  it("uses singular 'document' for count of 1", () => {
    render(<ContextBanner context={{ folderName: "Budget", docCount: 1 }} />);
    expect(screen.getByText(/1/)).toBeInTheDocument();
    expect(screen.getByText(/document\b/)).toBeInTheDocument();
    expect(screen.queryByText(/documents/)).toBeNull();
  });

  it("renders nothing when context is null", () => {
    const { container } = render(<ContextBanner context={null} />);
    expect(container.firstChild).toBeNull();
  });
});
