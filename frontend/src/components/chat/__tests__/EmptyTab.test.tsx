import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { EmptyTab } from "../EmptyTab";

describe("EmptyTab", () => {
  it("renders title and body as a centred empty state", () => {
    render(<EmptyTab title="No document open" body="Click any uploaded document in the sidebar." />);
    expect(screen.getByText("No document open")).toBeInTheDocument();
    expect(
      screen.getByText("Click any uploaded document in the sidebar."),
    ).toBeInTheDocument();
  });

  it("renders title as a heading element", () => {
    render(<EmptyTab title="Empty" body="..." />);
    const heading = screen.getByRole("heading", { name: "Empty" });
    expect(heading.tagName).toBe("H3");
  });
});
