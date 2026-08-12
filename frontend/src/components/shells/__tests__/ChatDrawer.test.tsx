import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ChatDrawer } from "@/components/shells/ChatDrawer";

describe("ChatDrawer", () => {
  it("renders expanded with its children visible when defaultState is open", () => {
    render(
      <ChatDrawer side="right" defaultState="open" label="Chat">
        <div data-testid="drawer-child">hello</div>
      </ChatDrawer>,
    );
    const drawer = screen.getByTestId("chat-drawer");
    expect(drawer.getAttribute("data-open")).toBe("true");
    expect(drawer.getAttribute("data-side")).toBe("right");
    expect(screen.getByTestId("drawer-child")).not.toBeNull();
  });

  it("starts collapsed when minimised and expands on toggle click", () => {
    render(
      <ChatDrawer side="left" defaultState="minimised">
        <div>body</div>
      </ChatDrawer>,
    );
    const drawer = screen.getByTestId("chat-drawer");
    expect(drawer.getAttribute("data-open")).toBe("false");
    expect(drawer.getAttribute("data-side")).toBe("left");

    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("chat-drawer").getAttribute("data-open")).toBe("true");
  });

  it("collapses an open drawer when Escape is pressed", () => {
    render(
      <ChatDrawer side="right" defaultState="open">
        <div>body</div>
      </ChatDrawer>,
    );
    expect(screen.getByTestId("chat-drawer").getAttribute("data-open")).toBe("true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByTestId("chat-drawer").getAttribute("data-open")).toBe("false");
  });
});
