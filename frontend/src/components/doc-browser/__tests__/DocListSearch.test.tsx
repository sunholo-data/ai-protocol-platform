import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DocListSearch } from "../DocListSearch";

describe("DocListSearch", () => {
  it("renders input with placeholder", () => {
    render(<DocListSearch value="" onChange={vi.fn()} />);
    expect(screen.getByPlaceholderText(/search documents/i)).toBeInTheDocument();
  });

  it("calls onChange when typing", async () => {
    const onChange = vi.fn();
    render(<DocListSearch value="" onChange={onChange} />);
    await userEvent.type(screen.getByRole("searchbox"), "abc");
    expect(onChange).toHaveBeenCalled();
  });

  it("displays current value", () => {
    render(<DocListSearch value="report" onChange={vi.fn()} />);
    expect(screen.getByDisplayValue("report")).toBeInTheDocument();
  });
});
