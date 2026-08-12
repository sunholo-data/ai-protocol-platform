// Sprint 1.25 — wires the second iframe→host RPC channel
// (`ui/update-model-context`). AppRenderer surfaces it via
// `onFallbackRequest` (the catch-all for JSON-RPC methods AppRenderer
// doesn't route specifically). MCPAppToolCallRouter dispatches on the
// method name and POSTs to /api/proxy/api/sessions/{id}/iframe-context.
//
// Tests assert:
//   * known method + sessionId set → POST fires with the expected body
//   * known method + sessionId missing → silently no-op (no fetch)
//   * other methods are ignored
//   * fetch failures are swallowed (the iframe never sees a backend
//     transport error — graceful degradation: agent stays blind to
//     iframe state, but the iframe keeps rendering)

import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import toolsListFixture from "./fixtures/map-server-tools-list.json";
import showMapResultFixture from "./fixtures/map-server-show-map-result.json";
import uiResourceFixture from "./fixtures/map-server-ui-resource.json";
import type { ToolCallState } from "@/hooks/useSkillAgent";

type FallbackHandler = (req: {
  method: string;
  params?: Record<string, unknown>;
}) => Promise<Record<string, unknown>>;

let capturedOnFallbackRequest: FallbackHandler | null = null;

vi.mock("@mcp-ui/client", async () => {
  const actual =
    await vi.importActual<typeof import("@mcp-ui/client")>("@mcp-ui/client");
  return {
    ...actual,
    AppRenderer: (props: { onFallbackRequest?: FallbackHandler }) => {
      capturedOnFallbackRequest = props.onFallbackRequest ?? null;
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

const fetchWithAuthMock = vi.fn(async () => new Response(null, { status: 204 }));
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuthMock(...(args as [])),
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

const SAMPLE_PARAMS = {
  structuredContent: {
    viewUUID: "abc-123",
    currentBounds: { west: 12.4101, south: 55.5267, east: 12.7301, north: 55.8467 },
    label: "Copenhagen",
  },
};

describe("MCPAppToolCallRouter — ui/update-model-context (sprint 1.25)", () => {
  it("POSTs to /iframe-context when sessionId is set and method matches", async () => {
    capturedOnFallbackRequest = null;
    fetchWithAuthMock.mockClear();

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["ext-apps-map"]}
        sessionId="sess-42"
      />,
    );
    await waitFor(() => expect(capturedOnFallbackRequest).not.toBeNull());

    const ack = await capturedOnFallbackRequest!({
      method: "ui/update-model-context",
      params: SAMPLE_PARAMS,
    });

    expect(ack).toEqual({});
    expect(fetchWithAuthMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchWithAuthMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      "/api/proxy/api/sessions/sess-42/iframe-context",
    );
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body));
    expect(body.serverId).toBe("ext-apps-map");
    expect(body.toolName).toBe("show-map");
    expect(body.structuredContent).toEqual(SAMPLE_PARAMS.structuredContent);
  });

  it("does NOT POST when sessionId is missing (graceful no-op)", async () => {
    capturedOnFallbackRequest = null;
    fetchWithAuthMock.mockClear();

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["ext-apps-map"]}
        // sessionId omitted intentionally
      />,
    );
    await waitFor(() => expect(capturedOnFallbackRequest).not.toBeNull());

    const ack = await capturedOnFallbackRequest!({
      method: "ui/update-model-context",
      params: SAMPLE_PARAMS,
    });

    expect(ack).toEqual({});
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it("ignores fallback requests for other methods (no POST)", async () => {
    capturedOnFallbackRequest = null;
    fetchWithAuthMock.mockClear();

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["ext-apps-map"]}
        sessionId="sess-42"
      />,
    );
    await waitFor(() => expect(capturedOnFallbackRequest).not.toBeNull());

    await capturedOnFallbackRequest!({
      method: "some/other/method",
      params: {},
    });

    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it("swallows POST failures so the iframe never sees them", async () => {
    capturedOnFallbackRequest = null;
    fetchWithAuthMock.mockClear();
    fetchWithAuthMock.mockRejectedValueOnce(new Error("network down"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["ext-apps-map"]}
        sessionId="sess-42"
      />,
    );
    await waitFor(() => expect(capturedOnFallbackRequest).not.toBeNull());

    const ack = await capturedOnFallbackRequest!({
      method: "ui/update-model-context",
      params: SAMPLE_PARAMS,
    });

    // Iframe got an empty {} ack — no error propagated. Backend
    // failure logged, agent will be blind to this push, iframe keeps
    // working (graceful degradation per the design doc).
    expect(ack).toEqual({});
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});
