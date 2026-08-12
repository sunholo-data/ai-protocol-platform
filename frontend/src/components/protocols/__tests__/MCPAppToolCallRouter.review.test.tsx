// Sprint 2.13 M2 — ArtefactReviewer integration tests for
// MCPAppToolCallRouter. Covers the four decision paths plus the
// fail-open + soft-budget fallbacks.
//
// The router consults getArtefactReviewer().review(...) AFTER
// readResource resolves and BEFORE <AppRenderer> mounts. Tests
// register a fake reviewer via setArtefactReviewer() and assert
// the rendered output.

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import toolsListFixture from "./fixtures/map-server-tools-list.json";
import showMapResultFixture from "./fixtures/map-server-show-map-result.json";
import uiResourceFixture from "./fixtures/map-server-ui-resource.json";
import type { ToolCallState } from "@/hooks/useSkillAgent";
import {
  type ArtefactDecision,
  clearArtefactReviewer,
  setArtefactReviewer,
} from "@/components/protocols/ArtefactReviewer";

// Mock AppRenderer — record props we'd have rendered with.
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

// Mock useMcpClient.
const useMcpClientMock = vi.fn();
vi.mock("@/lib/mcpClient", () => ({
  useMcpClient: (...args: unknown[]) => useMcpClientMock(...args),
}));

// Mock fetchWithAuth so the ArtefactRefused audit POST doesn't try to
// hit a real endpoint. Track calls for the audit-fired assertion.
const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
  new Response("", { status: 200 }),
);
vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: (url: string, init?: RequestInit) => fetchMock(url, init),
}));

import { MCPAppToolCallRouter } from "@/components/protocols/MCPAppToolCallRouter";

function fakeClient() {
  return {
    listTools: vi.fn(async () => toolsListFixture.result),
    readResource: vi.fn(async () => uiResourceFixture.result),
  };
}

function showMapToolCall(id = "tc-1"): ToolCallState {
  return {
    id,
    name: "show-map",
    status: "success",
    resultContent: JSON.stringify(showMapResultFixture.result),
  };
}

describe("MCPAppToolCallRouter — ArtefactReviewer integration (sprint 2.13)", () => {
  beforeEach(() => {
    appRendererMock.mockClear();
    fetchMock.mockClear();
    clearArtefactReviewer();
  });
  afterEach(() => {
    clearArtefactReviewer();
  });

  it("approve path: renders <AppRenderer> unchanged (back-compat)", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);

    // Permissive default — no reviewer registered.
    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("app-renderer")).toBeInTheDocument());
    expect(screen.queryByTestId("artefact-refused")).not.toBeInTheDocument();
    expect(screen.queryByTestId("artefact-warning-stripe")).not.toBeInTheDocument();
  });

  it("warn path: ArtefactWarningStripe wraps <AppRenderer>; BOTH are mounted", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    setArtefactReviewer({
      async review() {
        return {
          action: "warn",
          message: "Review pending",
          reasonCode: "REVIEW_PENDING",
        };
      },
    });

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("app-renderer")).toBeInTheDocument());
    expect(screen.getByTestId("artefact-warning-stripe")).toHaveTextContent("Review pending");
    expect(screen.getByTestId("artefact-warning-reason")).toHaveTextContent("REVIEW_PENDING");
  });

  it("block path: <ArtefactRefused> renders; <AppRenderer> is NOT mounted", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    setArtefactReviewer({
      async review() {
        return {
          action: "block",
          message: "Contains forbidden <script> tag",
          reasonCode: "FORBIDDEN_TAG",
          appealUrl: "https://example.com/appeal",
        };
      },
    });

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("artefact-refused")).toBeInTheDocument());
    expect(screen.queryByTestId("app-renderer")).not.toBeInTheDocument();
    expect(appRendererMock).not.toHaveBeenCalled();
    expect(screen.getByText("Contains forbidden <script> tag")).toBeInTheDocument();
    expect(screen.getByTestId("artefact-refused-reason")).toHaveTextContent("FORBIDDEN_TAG");
    expect(screen.getByTestId("artefact-refused-appeal")).toHaveAttribute(
      "href",
      "https://example.com/appeal",
    );
  });

  it("block path fires audit POST on mount when sessionId is set", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    setArtefactReviewer({
      async review() {
        return {
          action: "block",
          message: "blocked",
          reasonCode: "TEST_BLOCK",
        };
      },
    });

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall("tc-block-audit")]}
        mcpServerIds={["map-server"]}
        sessionId="sess-abc"
      />,
    );

    await waitFor(() => expect(screen.getByTestId("artefact-refused")).toBeInTheDocument());
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.at(-1)!;
    const [url, init] = call;
    expect(url).toBe("/api/proxy/api/sessions/sess-abc/artefact-blocked");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({
      tool_name: "show-map",
      server_id: "map-server",
      reason_code: "TEST_BLOCK",
      invocation_id: "tc-block-audit",
    });
  });

  it("reviewer-crash path: falls back to approve + <AppRenderer> still mounts", async () => {
    const client = fakeClient();
    useMcpClientMock.mockReturnValue(client);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    setArtefactReviewer({
      async review() {
        throw new Error("boom");
      },
    });

    render(
      <MCPAppToolCallRouter
        toolCalls={[showMapToolCall()]}
        mcpServerIds={["map-server"]}
      />,
    );

    await waitFor(() => expect(screen.getByTestId("app-renderer")).toBeInTheDocument());
    expect(screen.queryByTestId("artefact-refused")).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("slow-reviewer (>500ms) path: degrades to approve + warn log", async () => {
    vi.useFakeTimers();
    try {
      const client = fakeClient();
      useMcpClientMock.mockReturnValue(client);
      const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
      let resolveLater: ((d: ArtefactDecision) => void) | undefined;
      setArtefactReviewer({
        async review() {
          return new Promise<ArtefactDecision>((resolve) => {
            resolveLater = resolve;
          });
        },
      });

      render(
        <MCPAppToolCallRouter
          toolCalls={[showMapToolCall("tc-slow")]}
          mcpServerIds={["map-server"]}
        />,
      );

      // Advance past the soft budget — the reviewer is still pending.
      await vi.advanceTimersByTimeAsync(600);

      // Switch to real timers so React's act + waitFor can settle the
      // post-degradation state-set without our fake timer freezing them.
      vi.useRealTimers();
      await waitFor(() => expect(screen.getByTestId("app-renderer")).toBeInTheDocument());
      expect(
        consoleWarn.mock.calls.some((c) =>
          String(c[0] ?? "").includes("ArtefactReviewer"),
        ),
      ).toBe(true);
      // Resolve the lingering promise — should be a no-op (cancelled flag prevents state mutation).
      resolveLater?.({ action: "block", message: "late", reasonCode: "LATE" });
      // Give the lingering then-handler a tick.
      await new Promise((r) => setTimeout(r, 5));
      expect(screen.queryByTestId("artefact-refused")).not.toBeInTheDocument();
      consoleWarn.mockRestore();
    } finally {
      vi.useRealTimers();
    }
  });
});
