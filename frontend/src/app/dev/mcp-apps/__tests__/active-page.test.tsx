// Vitest for /dev/mcp-apps/active — exercises the synthetic-notification
// button panel + adapter integration without a real iframe. Locks the
// contract that:
//  (a) known notification shapes flow through the adapter to the log
//  (b) unknown shapes log "null (adapter ignored)" rather than crashing

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Stub the router so the test doesn't try to mount a real <AppRenderer>
// (which needs a live iframe + sandbox proxy on a different origin).
vi.mock("@/components/protocols/MCPAppToolCallRouter", () => ({
  MCPAppToolCallRouter: () => <div data-testid="stub-router" />,
}));

import McpAppsActivePage from "../active/page";

describe("/dev/mcp-apps/active", () => {
  it("renders the synthetic-notification button panel + iframe stub + empty log", () => {
    render(<McpAppsActivePage />);
    expect(screen.getByText(/MCP Apps active smoke/i)).toBeInTheDocument();
    expect(screen.getByTestId("stub-router")).toBeInTheDocument();
    expect(screen.getByText(/fire: location-selected \(Munich\)/i)).toBeInTheDocument();
    expect(screen.getByText(/No notifications yet/i)).toBeInTheDocument();
  });

  it("location-selected button → adapter translates → 'Tell me more about Munich' in log", () => {
    render(<McpAppsActivePage />);
    fireEvent.click(screen.getByText(/fire: location-selected \(Munich\)/i));
    expect(screen.getByText(/Tell me more about Munich/i)).toBeInTheDocument();
  });

  it("route-selected button → adapter translates the from/to pair", () => {
    render(<McpAppsActivePage />);
    fireEvent.click(screen.getByText(/fire: route-selected/i));
    expect(
      screen.getByText(/Tell me about the route from Munich to Paris/i),
    ).toBeInTheDocument();
  });

  it("unknown-shape button → adapter returns null → log shows 'null (adapter ignored)'", () => {
    render(<McpAppsActivePage />);
    fireEvent.click(screen.getByText(/fire: unknown-shape/i));
    expect(screen.getByText(/null \(adapter ignored\)/i)).toBeInTheDocument();
  });

  it("malformed payload button → adapter returns null without throwing", () => {
    render(<McpAppsActivePage />);
    // Should not throw. Asserting via getByText also implicitly asserts no
    // unhandled error tore down the tree.
    fireEvent.click(screen.getByText(/fire: malformed/i));
    expect(screen.getByText(/null \(adapter ignored\)/i)).toBeInTheDocument();
  });

  it("clear log button empties the log", () => {
    render(<McpAppsActivePage />);
    fireEvent.click(screen.getByText(/fire: location-selected \(Munich\)/i));
    expect(screen.queryByText(/No notifications yet/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/clear log/i));
    expect(screen.getByText(/No notifications yet/i)).toBeInTheDocument();
  });
});
