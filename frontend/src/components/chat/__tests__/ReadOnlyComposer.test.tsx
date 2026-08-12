import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ReadOnlyComposer from "../ReadOnlyComposer";

describe("ReadOnlyComposer", () => {
  it("renders the read-only message", () => {
    render(<ReadOnlyComposer onContinue={vi.fn()} />);
    expect(screen.getByText(/viewing a shared conversation/i)).toBeInTheDocument();
  });

  it("shows the Continue from here button", () => {
    render(<ReadOnlyComposer onContinue={vi.fn()} />);
    expect(screen.getByRole("button", { name: /continue from here/i })).toBeInTheDocument();
  });

  it("calls onContinue when the button is clicked", () => {
    const onContinue = vi.fn();
    render(<ReadOnlyComposer onContinue={onContinue} />);
    fireEvent.click(screen.getByRole("button", { name: /continue from here/i }));
    expect(onContinue).toHaveBeenCalledOnce();
  });
});
