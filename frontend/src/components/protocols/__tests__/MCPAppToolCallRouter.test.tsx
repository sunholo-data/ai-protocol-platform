// M2A — router that decides whether a tool call has a UI surface and, if so,
// mounts <AppRenderer> from @mcp-ui/client wired to the correct MCP Client.
//
// Spec is UI-by-REFERENCE: the tool DEFINITION (from tools/list) carries
// _meta.ui.resourceUri; the tool RESULT carries data only. We fetch tool
// defs lazily via client.listTools() the first time we see a tool call for
// that server, cache them, and decide based on _meta.ui.
//
// Tests mock <AppRenderer> from @mcp-ui/client so we don't try to mount
// a real iframe in jsdom. We assert:
//   * shows AppRenderer for show-map (has _meta.ui.resourceUri)
//   * skips geocode (no _meta.ui)
//   * returns null when client not yet connected
//   * survives malformed resultContent (JSON parse failure)
//   * extracts serverId via tool-name prefix when present

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import toolsListFixture from "./fixtures/map-server-tools-list.json";
import showMapResultFixture from "./fixtures/map-server-show-map-result.json";
import uiResourceFixture from "./fixtures/map-server-ui-resource.json";
import type { ToolCallState } from "@/hooks/useSkillAgent";

// Mock AppRenderer — capture props we render with so we can assert on them.
const appRendererMock = vi.fn((props: Record<string, unknown>) => (
  <div data-testid="app-renderer" data-tool-name={String(props.toolName)} />
));
vi.mock("@mcp-ui/client", async () => {
  const actual =
    await vi.importActual<typeof import("@mcp-ui/client")>("@mcp-ui/client");
  return {
    ...actual,
    AppRenderer: (props: Record<string, unknown>) => appRendererMock(props),
  };
});

// Mock the mcpClient hook so we control whether the Client is "ready" and
// what its tools/list returns.
const useMcpClientMock = vi.fn();
vi.mock("@/lib/mcpClient", () => ({
  useMcpClient: (...args: unknown[]) => useMcpClientMock(...args),
}));

import { MCPAppToolCallRouter } from "@/components/protocols/MCPAppToolCallRouter";

function makeListTools(toolsListResult = toolsListFixture.result) {
  return vi.fn(async () => toolsListResult);
}

function makeReadResource(resourceResult = uiResourceFixture.result) {
  return vi.fn(async () => resourceResult);
}

function fakeClient() {
  return {
    listTools: makeListTools(),
    readResource: makeReadResource(),
  };
}

function tc(
  name: string,
  resultContent: string | undefined,
  id = `tc-${name}`,
): ToolCallState {
  return { id, name, status: "success", resultContent };
}

describe("MCPAppToolCallRouter", () => {
  it("renders <AppRenderer> for a UI-bearing tool (show-map)", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    appRendererMock.mockClear();

    const toolCalls: ToolCallState[] = [
      tc(
        "show-map",
        JSON.stringify(showMapResultFixture.result),
      ),
    ];

    render(
      <MCPAppToolCallRouter
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("app-renderer")).toBeInTheDocument();
    });
    expect(appRendererMock).toHaveBeenCalled();
    const lastCall = appRendererMock.mock.calls.at(-1)?.[0] ?? {};
    expect(lastCall.toolName).toBe("show-map");
    expect(lastCall.client).toBe(client);
    expect(lastCall.toolResult).toMatchObject({
      content: showMapResultFixture.result.content,
    });
  });

  it("returns null for a tool without _meta.ui (geocode)", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    appRendererMock.mockClear();

    const toolCalls: ToolCallState[] = [
      tc("geocode", JSON.stringify({ result: "data" })),
    ];

    const { container } = render(
      <MCPAppToolCallRouter
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );

    // give the listTools cache a tick
    await waitFor(() =>
      expect(client.listTools).toHaveBeenCalled(),
    );
    expect(appRendererMock).not.toHaveBeenCalled();
    expect(container.querySelector("[data-testid='app-renderer']")).toBeNull();
  });

  it("returns null for every tool call when client is null (still connecting)", () => {
    useMcpClientMock.mockReturnValue(null);
    appRendererMock.mockClear();

    const toolCalls: ToolCallState[] = [
      tc("show-map", JSON.stringify(showMapResultFixture.result)),
    ];

    const { container } = render(
      <MCPAppToolCallRouter
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );
    expect(appRendererMock).not.toHaveBeenCalled();
    expect(container.querySelector("[data-testid='app-renderer']")).toBeNull();
  });

  it("survives malformed resultContent (JSON parse failure) without crashing", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    appRendererMock.mockClear();

    const toolCalls: ToolCallState[] = [
      tc("show-map", "not valid json {{{"),
    ];

    // Should NOT throw; should render AppRenderer with toolResult=undefined
    // (the iframe will degrade gracefully).
    expect(() =>
      render(
        <MCPAppToolCallRouter
          toolCalls={toolCalls}
          mcpServerIds={["map-server"]}
        />,
      ),
    ).not.toThrow();

    await waitFor(() => {
      expect(screen.queryByTestId("app-renderer")).toBeInTheDocument();
    });
    const lastCall = appRendererMock.mock.calls.at(-1)?.[0] ?? {};
    expect(lastCall.toolResult).toBeUndefined();
  });

  it("understands tool-name prefix '<server_id>_<tool_name>' (ADK default)", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    appRendererMock.mockClear();

    const toolCalls: ToolCallState[] = [
      tc(
        "map-server_show-map",
        JSON.stringify(showMapResultFixture.result),
      ),
    ];

    render(
      <MCPAppToolCallRouter
        toolCalls={toolCalls}
        mcpServerIds={["map-server"]}
      />,
    );
    await waitFor(() => expect(screen.queryByTestId("app-renderer")).toBeInTheDocument());
    const lastCall = appRendererMock.mock.calls.at(-1)?.[0] ?? {};
    // The unprefixed name is what AppRenderer/MCP server expects.
    expect(lastCall.toolName).toBe("show-map");
  });

  it("renders nothing when toolCalls is empty", () => {
    useMcpClientMock.mockReturnValue(fakeClient());
    appRendererMock.mockClear();

    const { container } = render(
      <MCPAppToolCallRouter
        toolCalls={[]}
        mcpServerIds={["map-server"]}
      />,
    );
    expect(container.querySelector("[data-testid='app-renderer']")).toBeNull();
    expect(appRendererMock).not.toHaveBeenCalled();
  });
});
