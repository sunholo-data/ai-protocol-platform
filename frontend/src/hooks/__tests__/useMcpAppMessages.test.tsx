// Tests for useMcpAppMessages.
// Auth by origin (the sandbox proxy has a real origin per MCP Apps spec
// §Sandbox proxy line 474), JSON-RPC envelope shape. This is the
// spec-compliant listener primitive — the platform has no
// off-spec/window-identity fallback (see ADR-013).

import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMcpAppMessages } from "@/hooks/useMcpAppMessages";

const SANDBOX_ORIGIN = "http://localhost:3457";

interface HarnessProps {
  onNotification: (params: Record<string, unknown>) => void;
  method?: string;
  sandboxOrigin?: string;
}

function Harness({
  onNotification,
  method = "ui/update-model-context",
  sandboxOrigin = SANDBOX_ORIGIN,
}: HarnessProps) {
  useMcpAppMessages({ sandboxOrigin, method, onNotification });
  return <div data-testid="harness" />;
}

function dispatch(data: unknown, origin = SANDBOX_ORIGIN) {
  window.dispatchEvent(new MessageEvent("message", { data, origin }));
}

describe("useMcpAppMessages", () => {
  afterEach(() => {
    // listener cleanup is via render's unmount; nothing global to reset
  });

  it("calls onNotification when the JSON-RPC notification matches method and origin", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} />);
    dispatch({
      jsonrpc: "2.0",
      method: "ui/update-model-context",
      params: { structuredContent: { v0: 15 } },
    });
    expect(onNotification).toHaveBeenCalledTimes(1);
    expect(onNotification.mock.calls[0][0]).toEqual({ structuredContent: { v0: 15 } });
  });

  it("rejects events whose origin doesn't match sandboxOrigin", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} />);
    dispatch(
      { jsonrpc: "2.0", method: "ui/update-model-context", params: {} },
      "https://evil.example.com",
    );
    expect(onNotification).not.toHaveBeenCalled();
  });

  it("rejects events without jsonrpc: 2.0 envelope", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} />);
    dispatch({ method: "ui/update-model-context", params: {} }); // no jsonrpc field
    dispatch({ jsonrpc: "1.0", method: "ui/update-model-context", params: {} });
    expect(onNotification).not.toHaveBeenCalled();
  });

  it("rejects notifications with the wrong method (coexisting handlers in the same page)", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} method="ui/update-model-context" />);
    dispatch({
      jsonrpc: "2.0",
      method: "ui/notifications/initialized",
      params: {},
    });
    dispatch({ jsonrpc: "2.0", method: "tools/call", params: {} });
    expect(onNotification).not.toHaveBeenCalled();
  });

  it("handles a notification with no params (treated as empty object)", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} />);
    dispatch({ jsonrpc: "2.0", method: "ui/update-model-context" });
    expect(onNotification).toHaveBeenCalledWith({});
  });

  it("removes the listener on unmount", () => {
    const onNotification = vi.fn();
    const { unmount } = render(<Harness onNotification={onNotification} />);
    unmount();
    dispatch({
      jsonrpc: "2.0",
      method: "ui/update-model-context",
      params: { v: 1 },
    });
    expect(onNotification).not.toHaveBeenCalled();
  });

  it("dev-mode logs each accepted notification under the method label", () => {
    const onNotification = vi.fn();
    const consoleSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    render(<Harness onNotification={onNotification} />);
    dispatch({
      jsonrpc: "2.0",
      method: "ui/update-model-context",
      params: { v: 1 },
    });
    expect(consoleSpy).toHaveBeenCalledWith(
      "[ui/update-model-context]",
      expect.objectContaining({ v: 1 }),
    );
    consoleSpy.mockRestore();
  });

  it("does not log when an event is rejected (origin mismatch)", () => {
    const onNotification = vi.fn();
    const consoleSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    render(<Harness onNotification={onNotification} />);
    dispatch(
      { jsonrpc: "2.0", method: "ui/update-model-context", params: {} },
      "https://evil.example.com",
    );
    expect(consoleSpy).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it("tolerates trailing slash on sandboxOrigin", () => {
    const onNotification = vi.fn();
    render(<Harness onNotification={onNotification} sandboxOrigin="http://localhost:3457/" />);
    dispatch(
      { jsonrpc: "2.0", method: "ui/update-model-context", params: {} },
      "http://localhost:3457",
    );
    expect(onNotification).toHaveBeenCalledTimes(1);
  });
});
