import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/components/chat/ChatMarkdown", () => ({
  ChatMarkdown: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

import { PinnedWelcome } from "../PinnedWelcome";

beforeEach(() => {
  window.sessionStorage.clear();
});

describe("PinnedWelcome", () => {
  it("renders the content body via ChatMarkdown when expanded (default)", () => {
    render(<PinnedWelcome content="Try clicking an example PPA." skillId="one-ppa-expert" />);
    expect(screen.getByTestId("markdown")).toHaveTextContent("Try clicking an example PPA.");
  });

  it("renders nothing when content is empty (component is a no-op for skills without an intro)", () => {
    const { container } = render(<PinnedWelcome content="" skillId="x" />);
    expect(container.firstChild).toBeNull();
  });

  it("toggles collapsed state on header click and persists per-skillId", () => {
    render(<PinnedWelcome content="hello" skillId="skill-a" />);
    // Expanded by default
    expect(screen.getByTestId("markdown")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByTestId("markdown")).toBeNull();
    expect(window.sessionStorage.getItem("aitana.welcome.collapsed:skill-a")).toBe("1");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("markdown")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("aitana.welcome.collapsed:skill-a")).toBe("0");
  });

  it("restores collapsed state from sessionStorage on mount (per-skill)", () => {
    window.sessionStorage.setItem("aitana.welcome.collapsed:skill-b", "1");
    render(<PinnedWelcome content="hello" skillId="skill-b" />);
    // Should be collapsed on mount
    expect(screen.queryByTestId("markdown")).toBeNull();
  });

  it("collapse state on skill-a does not affect skill-b (per-skill key scoping)", () => {
    window.sessionStorage.setItem("aitana.welcome.collapsed:skill-a", "1");
    render(<PinnedWelcome content="hello" skillId="skill-b" />);
    // skill-b has no stored state → expanded by default
    expect(screen.getByTestId("markdown")).toBeInTheDocument();
  });

  it("uses skillDisplayName in the default header when provided", () => {
    render(<PinnedWelcome content="x" skillId="s" skillDisplayName="Contract Expert" />);
    expect(screen.getByRole("button")).toHaveTextContent(/Contract Expert/);
  });

  it("uses headerLabel when explicitly set (overrides skillDisplayName)", () => {
    render(
      <PinnedWelcome
        content="x"
        skillId="s"
        skillDisplayName="Contract Expert"
        headerLabel="How this works"
      />,
    );
    expect(screen.getByRole("button")).toHaveTextContent("How this works");
    expect(screen.getByRole("button")).not.toHaveTextContent("Contract Expert");
  });
});
