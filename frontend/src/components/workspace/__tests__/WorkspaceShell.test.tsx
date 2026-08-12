import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { WorkspaceShell } from "../WorkspaceShell";

describe("WorkspaceShell", () => {
  it("renders chat in the left pane and workbench in the right pane", () => {
    render(
      <WorkspaceShell
        chat={<div data-testid="chat-content">CHAT</div>}
        workbench={<div data-testid="workbench-content">WORKBENCH</div>}
      />,
    );

    const chat = screen.getByTestId("workspace-chat-pane");
    const workbench = screen.getByTestId("workspace-workbench-pane");
    expect(chat).toContainElement(screen.getByTestId("chat-content"));
    expect(workbench).toContainElement(screen.getByTestId("workbench-content"));
  });

  it("renders a vertical divider with separator role", () => {
    render(<WorkspaceShell chat={<div />} workbench={<div />} />);
    const divider = screen.getByRole("separator");
    expect(divider).toHaveAttribute("aria-orientation", "vertical");
  });

  it("clamps the initial fraction within [minChatFraction, maxChatFraction]", () => {
    render(
      <WorkspaceShell
        chat={<div />}
        workbench={<div />}
        initialChatFraction={0.9}
        minChatFraction={0.3}
        maxChatFraction={0.6}
      />,
    );
    const divider = screen.getByRole("separator");
    // Clamped to maxChatFraction
    expect(divider).toHaveAttribute("aria-valuenow", "0.6");
  });

  it("aria-valuemin and aria-valuemax reflect the configured bounds", () => {
    render(
      <WorkspaceShell
        chat={<div />}
        workbench={<div />}
        initialChatFraction={0.4}
        minChatFraction={0.15}
        maxChatFraction={0.75}
      />,
    );
    const divider = screen.getByRole("separator");
    expect(divider).toHaveAttribute("aria-valuemin", "0.15");
    expect(divider).toHaveAttribute("aria-valuemax", "0.75");
  });
});
