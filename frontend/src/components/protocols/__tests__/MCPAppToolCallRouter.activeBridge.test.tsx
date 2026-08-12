// M2A — active iframe→host bridge integration test
//
// Mounts the router with a stub AppRenderer that captures the `onMessage`
// callback. Fires fake notifications and asserts the host's onChatMessage
// callback is invoked for known shapes (and NOT invoked for unknown ones).
//
// This is the "active integration" the spec calls out: clicking inside the
// MCP App iframe should produce a new chat turn. We verify the wiring at
// the boundary — the parser is unit-tested separately in
// mcpAppNotificationAdapter.test.ts.

import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import toolsListFixture from "./fixtures/map-server-tools-list.json";
import showMapResultFixture from "./fixtures/map-server-show-map-result.json";
import uiResourceFixture from "./fixtures/map-server-ui-resource.json";
import type { ToolCallState } from "@/hooks/useSkillAgent";

// Capture the onMessage AppRenderer is mounted with so we can fire fake
// guest-iframe notifications at it.
type OnMessageFn = (params: unknown) => Promise<Record<string, unknown>>;
let capturedOnMessage: OnMessageFn | null = null;

vi.mock("@mcp-ui/client", async () => {
  const actual =
    await vi.importActual<typeof import("@mcp-ui/client")>("@mcp-ui/client");
  return {
    ...actual,
    AppRenderer: (props: { onMessage?: OnMessageFn }) => {
      capturedOnMessage = props.onMessage ?? null;
      return <div data-testid="app-renderer-stub" />;
    },
  };
});

const useMcpClientMock = vi.fn(() => ({
  listTools: vi.fn(async () => toolsListFixture.result),
  readResource: vi.fn(async () => uiResourceFixture.result),
}));
vi.mock("@/lib/mcpClient", () => ({
  useMcpClient: () => useMcpClientMock(),
}));

import { MCPAppToolCallRouter } from "@/components/protocols/MCPAppToolCallRouter";

function showMapToolCall(): ToolCallState {
  return {
    id: "tc-show-map-1",
    name: "show-map",
    status: "success",
    resultContent: JSON.stringify(showMapResultFixture.result),
  };
}

describe("MCPAppToolCallRouter — active iframe → host bridge", () => {
  it("known notification (location-selected) flows to onChatMessage with the templated string", async () => {
    capturedOnMessage = null;
    const onChatMessage = vi.fn();

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
        onChatMessage={onChatMessage}
      />,
    );

    await waitFor(() => expect(capturedOnMessage).not.toBeNull());

    await capturedOnMessage!({
      type: "app/notify",
      reason: "location-selected",
      payload: { location: "Munich" },
    });

    expect(onChatMessage).toHaveBeenCalledOnce();
    expect(onChatMessage).toHaveBeenCalledWith("Tell me more about Munich");
  });

  it("unknown notification shape does NOT call onChatMessage (silent forward-compat)", async () => {
    capturedOnMessage = null;
    const onChatMessage = vi.fn();

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
        onChatMessage={onChatMessage}
      />,
    );

    await waitFor(() => expect(capturedOnMessage).not.toBeNull());

    await capturedOnMessage!({
      type: "app/notify",
      reason: "future-event-we-dont-know",
      payload: { foo: "bar" },
    });
    await capturedOnMessage!("not even an object");
    await capturedOnMessage!(null);

    expect(onChatMessage).not.toHaveBeenCalled();
  });

  it("known notification with no onChatMessage handler is a safe no-op", async () => {
    capturedOnMessage = null;
    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
        // no onChatMessage prop
      />,
    );
    await waitFor(() => expect(capturedOnMessage).not.toBeNull());

    // Should resolve without throwing
    await expect(
      capturedOnMessage!({
        type: "app/notify",
        reason: "location-selected",
        payload: { location: "Berlin" },
      }),
    ).resolves.toEqual({});
  });
});
