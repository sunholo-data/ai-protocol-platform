import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ThinkingPanel } from "@/components/chat/ThinkingPanel";

describe("ThinkingPanel", () => {
  it("renders thinking content", () => {
    render(<ThinkingPanel content="Analysing the query..." isThinking={true} />);
    expect(screen.getByText("Analysing the query...")).toBeTruthy();
  });

  it("shows Thinking… label when isThinking is true", () => {
    render(<ThinkingPanel content="working" isThinking={true} />);
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });

  it("shows Thought process label when isThinking is false", () => {
    render(<ThinkingPanel content="done" isThinking={false} />);
    expect(screen.getByText("Thought process")).toBeTruthy();
  });

  it("auto-collapses (hides content) when isThinking transitions to false", () => {
    const { rerender } = render(
      <ThinkingPanel content="reasoning text" isThinking={true} />,
    );
    // Content visible while thinking
    expect(screen.getByText("reasoning text")).toBeTruthy();

    act(() => {
      rerender(<ThinkingPanel content="reasoning text" isThinking={false} />);
    });
    // Content hidden after collapse
    expect(screen.queryByText("reasoning text")).toBeFalsy();
  });

  it("toggle button expands and collapses content", () => {
    const { rerender } = render(
      <ThinkingPanel content="reasoning text" isThinking={true} />,
    );
    // Starts expanded — content visible
    expect(screen.getByText("reasoning text")).toBeTruthy();

    // Collapse via button click
    const btn = screen.getByRole("button");
    act(() => { btn.click(); });
    expect(screen.queryByText("reasoning text")).toBeFalsy();

    // Expand again
    act(() => { btn.click(); });
    expect(screen.getByText("reasoning text")).toBeTruthy();

    // If isThinking flips false while manually expanded — auto-collapses
    act(() => {
      rerender(<ThinkingPanel content="reasoning text" isThinking={false} />);
    });
    expect(screen.queryByText("reasoning text")).toBeFalsy();
  });
});
