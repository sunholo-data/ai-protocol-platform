// Tests for StaticArtefactFrame — the spec-compliant host wrapper for
// MCP-App static artefacts. The contract these tests pin:
//
//   1. Mounts iframe at ${sandboxOrigin}/sandbox.html with the right
//      sandbox attributes (allow-scripts + allow-same-origin on the
//      outer proxy frame, per spec §Sandbox proxy line 475).
//   2. On ui/notifications/sandbox-proxy-ready: fetches the artefact
//      HTML and sends ui/notifications/sandbox-resource-ready back
//      with {html, sandbox}.
//   3. Responds to artefact's ui/initialize with a McpUiInitializeResult
//      containing hostContext.
//   4. Forwards ui/update-model-context notifications to onUpdateModelContext.
//   5. Calls onInitialized when ui/notifications/initialized arrives.
//   6. Responds to ping requests with result: {}.
//   7. Rejects messages whose origin doesn't match sandboxOrigin.
//
// Ported from AIPLA fork (cphu-aipla-app) feature/mcp-app-spec-compliance
// branch, 2026-06-05 — covers the spec-pinning gap on a workshop-critical
// component. See [template-mcp-apps-artefacts.md].

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StaticArtefactFrame } from "../StaticArtefactFrame";

const SANDBOX_ORIGIN = "http://localhost:3457";
const ARTEFACT_PATH = "_template/v1";

// We hook the iframe's contentWindow.postMessage so tests can observe
// what the host sends to the proxy. jsdom gives us a real Window for
// the iframe, so we can spy on its postMessage directly.
function captureProxyPostMessage() {
  const captured: { msg: unknown; targetOrigin: string }[] = [];
  // Find the iframe AFTER it mounts; the captures are registered then.
  const setup = () => {
    const iframe = document.querySelector("iframe") as HTMLIFrameElement | null;
    if (!iframe?.contentWindow) return false;
    const orig = iframe.contentWindow.postMessage.bind(iframe.contentWindow);
    iframe.contentWindow.postMessage = ((msg: unknown, target: string | StructuredSerializeOptions) => {
      captured.push({
        msg,
        targetOrigin: typeof target === "string" ? target : "(opts)",
      });
      // Don't actually dispatch — the proxy isn't running in jsdom anyway.
      return orig as unknown as ReturnType<typeof Window.prototype.postMessage>;
    }) as typeof window.postMessage;
    return true;
  };
  return { captured, setup };
}

// Helper to dispatch a synthetic event "from the proxy" (origin == SANDBOX_ORIGIN).
function dispatchFromProxy(data: unknown) {
  window.dispatchEvent(new MessageEvent("message", { data, origin: SANDBOX_ORIGIN }));
}

describe("StaticArtefactFrame", () => {
  // Mock global fetch for the artefact HTML lookup. Tests that need a
  // specific response override per-test.
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve(new Response("<!doctype html><html><body>art</body></html>", { status: 200 })),
    ) as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("CONTENT-DRIVEN SIZING: sizes the iframe to the content height the proxy reports (host is cross-origin, cannot read it directly)", () => {
    // An <iframe> is a replaced element, so height:100% silently collapses to
    // 150px on any auto-height ancestor. The host is ALSO cross-origin with the
    // sandboxed artefact, so it can't read the content height itself. The proxy
    // (same-origin with the artefact via allow-same-origin) measures it and
    // posts a ui/notifications/size message; the host sizes the iframe to it.
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={vi.fn()}
        className="h-full"
      />,
    );
    const iframe = document.querySelector("iframe") as HTMLIFrameElement;
    // Full width; explicit, borderless. Before any report the fallback holds.
    expect(iframe.style.width).toBe("100%");
    expect(iframe.style.border).toBe("0px");
    expect(iframe.style.height).toBe("500px");

    // Proxy reports the measured content height → iframe grows to exactly it.
    act(() => {
      dispatchFromProxy({
        jsonrpc: "2.0",
        method: "ui/notifications/size",
        params: { height: 612 },
      });
    });
    expect(iframe.style.height).toBe("612px");

    // A later, taller report resizes again (content grew).
    act(() => {
      dispatchFromProxy({
        jsonrpc: "2.0",
        method: "ui/notifications/size",
        params: { height: 880.4 },
      });
    });
    // Ceil'd to whole pixels.
    expect(iframe.style.height).toBe("881px");
  });

  it("renders the iframe at ${sandboxOrigin}/sandbox.html with allow-scripts allow-same-origin", () => {
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
      />,
    );
    const iframe = screen.getByTitle(/MCP App artefact/i) as HTMLIFrameElement;
    expect(iframe.src).toBe(`${SANDBOX_ORIGIN}/sandbox.html`);
    const sandbox = iframe.getAttribute("sandbox") ?? "";
    expect(sandbox).toContain("allow-scripts");
    expect(sandbox).toContain("allow-same-origin");
  });

  it("strips trailing slash on sandboxOrigin", () => {
    render(
      <StaticArtefactFrame
        sandboxOrigin={`${SANDBOX_ORIGIN}/`}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
      />,
    );
    const iframe = screen.getByTitle(/MCP App artefact/i) as HTMLIFrameElement;
    expect(iframe.src).toBe(`${SANDBOX_ORIGIN}/sandbox.html`);
  });

  it("rejects messages from origins other than sandboxOrigin", () => {
    const onUpdateModelContext = vi.fn();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={onUpdateModelContext}
      />,
    );
    // Even with a valid envelope, wrong origin → reject.
    window.dispatchEvent(
      new MessageEvent("message", {
        data: {
          jsonrpc: "2.0",
          method: "ui/update-model-context",
          params: { structuredContent: { v: 1 } },
        },
        origin: "https://evil.example.com",
      }),
    );
    expect(onUpdateModelContext).not.toHaveBeenCalled();
  });

  it("forwards ui/update-model-context notifications to onUpdateModelContext (the main path)", () => {
    const onUpdateModelContext = vi.fn();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={onUpdateModelContext}
      />,
    );
    dispatchFromProxy({
      jsonrpc: "2.0",
      method: "ui/update-model-context",
      params: { structuredContent: { kind: "test.show_value", marker: "y_max" } },
    });
    expect(onUpdateModelContext).toHaveBeenCalledTimes(1);
    expect(onUpdateModelContext.mock.calls[0][0]).toEqual({
      kind: "test.show_value",
      marker: "y_max",
    });
  });

  it("ignores ui/update-model-context with no structuredContent (spec allows content-only; we currently only consume structuredContent)", () => {
    const onUpdateModelContext = vi.fn();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={onUpdateModelContext}
      />,
    );
    dispatchFromProxy({
      jsonrpc: "2.0",
      method: "ui/update-model-context",
      params: { content: [{ type: "text", text: "hi" }] }, // no structuredContent
    });
    expect(onUpdateModelContext).not.toHaveBeenCalled();
  });

  it("calls onInitialized when artefact sends ui/notifications/initialized", () => {
    const onInitialized = vi.fn();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        onInitialized={onInitialized}
      />,
    );
    dispatchFromProxy({
      jsonrpc: "2.0",
      method: "ui/notifications/initialized",
      params: { clientInfo: { name: "test-artefact", version: "1.0.0" } },
    });
    expect(onInitialized).toHaveBeenCalledWith({ name: "test-artefact", version: "1.0.0" });
  });

  it("fetches artefact HTML and sends sandbox-resource-ready when proxy says it's ready", async () => {
    const cap = captureProxyPostMessage();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
      />,
    );
    // Allow React to mount the iframe + the capture-spy to hook the
    // contentWindow.postMessage. captureProxyPostMessage spies AFTER mount.
    await waitFor(() => expect(cap.setup()).toBe(true));

    dispatchFromProxy({
      jsonrpc: "2.0",
      method: "ui/notifications/sandbox-proxy-ready",
      params: {},
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(`${SANDBOX_ORIGIN}/artefacts/${ARTEFACT_PATH}/index.html`);
    });
    await waitFor(() => {
      const resourceReady = cap.captured.find(
        (m) =>
          typeof m.msg === "object" &&
          m.msg !== null &&
          (m.msg as { method?: string }).method === "ui/notifications/sandbox-resource-ready",
      );
      expect(resourceReady).toBeDefined();
      const params = (resourceReady?.msg as { params?: { html?: string; sandbox?: string } })?.params;
      expect(params?.html).toContain("<!doctype html>");
      expect(params?.sandbox).toContain("allow-scripts");
    });
  });

  it("responds to ui/initialize with hostContext (handshake completion)", async () => {
    const cap = captureProxyPostMessage();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "dark", locale: "da-DK" }}
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));

    dispatchFromProxy({
      jsonrpc: "2.0",
      id: 1,
      method: "ui/initialize",
      params: {
        protocolVersion: "2026-01-26",
        capabilities: {},
        clientInfo: { name: "test-artefact", version: "1.0.0" },
      },
    });

    await waitFor(() => {
      const response = cap.captured.find(
        (m) => typeof m.msg === "object" && m.msg !== null && (m.msg as { id?: number }).id === 1,
      );
      expect(response).toBeDefined();
      const result = (response?.msg as { result?: { hostContext?: Record<string, unknown> } })
        ?.result;
      expect(result?.hostContext).toMatchObject({
        theme: "dark",
        locale: "da-DK",
        displayMode: "inline",
      });
    });
  });

  it("responds to ping requests with result: {} (spec line 508)", async () => {
    const cap = captureProxyPostMessage();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));

    dispatchFromProxy({ jsonrpc: "2.0", id: 42, method: "ping" });

    await waitFor(() => {
      const response = cap.captured.find(
        (m) => typeof m.msg === "object" && m.msg !== null && (m.msg as { id?: number }).id === 42,
      );
      expect(response).toBeDefined();
      expect((response?.msg as { result?: unknown }).result).toEqual({});
    });
  });

  it("ignores messages without jsonrpc: 2.0 envelope (defense against non-spec senders)", () => {
    const onUpdateModelContext = vi.fn();
    render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={onUpdateModelContext}
      />,
    );
    // Same origin, valid method, no jsonrpc field → rejected
    dispatchFromProxy({ method: "ui/update-model-context", params: { structuredContent: {} } });
    // Same origin, valid method, wrong jsonrpc version → rejected
    dispatchFromProxy({
      jsonrpc: "1.0",
      method: "ui/update-model-context",
      params: { structuredContent: {} },
    });
    expect(onUpdateModelContext).not.toHaveBeenCalled();
  });

  // ── G35 (template-mcp-apps-artefact-quality.md) ──────────────────────────
  // The host owns the theme channel. Initial value rides on the
  // ui/initialize handshake response (already covered by
  // "responds to ui/initialize with hostContext" above). Subsequent
  // changes are pushed via ui/update-theme notifications. The four
  // tests below pin the cases that matter:
  //   1. Pre-init prop changes are NOT pushed (init handshake hasn't
  //      finished; artefact reads theme from handshake response).
  //   2. Post-init changes DO push ui/update-theme.
  //   3. Identical-theme renders don't redundantly push (perf + correctness).
  //   4. The push uses the spec wire shape (JSON-RPC notification,
  //      method = ui/update-theme, params.theme = "light"|"dark").

  function signalInitialized() {
    // Helper: emulate the artefact's "I'm done initialising" notification
    // so the host-side handler flips initializedRef.current=true.
    dispatchFromProxy({
      jsonrpc: "2.0",
      method: "ui/notifications/initialized",
      params: { clientInfo: { name: "test-artefact", version: "1.0.0" } },
    });
  }

  it("G35: does NOT emit ui/update-theme before the artefact has initialised", async () => {
    const cap = captureProxyPostMessage();
    const { rerender } = render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "light" }}
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));

    // Change theme BEFORE init completes — must not push.
    rerender(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "dark" }}
      />,
    );

    const themeMsgs = cap.captured.filter(
      (m) => (m.msg as { method?: string })?.method === "ui/update-theme",
    );
    expect(themeMsgs).toHaveLength(0);
  });

  it("G35: emits ui/update-theme after init when host theme prop changes", async () => {
    const cap = captureProxyPostMessage();
    const { rerender } = render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "light" }}
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));

    // Complete init handshake — host sees ui/notifications/initialized.
    signalInitialized();

    // Now flip the theme. Should produce one ui/update-theme push with theme=dark.
    rerender(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "dark" }}
      />,
    );

    await waitFor(() => {
      const themeMsg = cap.captured.find(
        (m) => (m.msg as { method?: string })?.method === "ui/update-theme",
      );
      expect(themeMsg).toBeDefined();
      const msg = themeMsg!.msg as {
        jsonrpc: string;
        method: string;
        params: { theme: string };
      };
      expect(msg.jsonrpc).toBe("2.0");
      expect(msg.method).toBe("ui/update-theme");
      expect(msg.params.theme).toBe("dark");
      // Spec: ui/update-theme is a notification (no `id`).
      expect((themeMsg!.msg as { id?: number }).id).toBeUndefined();
    });
  });

  it("G35: does NOT re-emit ui/update-theme when the host re-renders with the same theme", async () => {
    const cap = captureProxyPostMessage();
    const { rerender } = render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "dark" }}
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));
    signalInitialized();

    // Re-render with the same theme several times.
    for (let i = 0; i < 3; i++) {
      rerender(
        <StaticArtefactFrame
          sandboxOrigin={SANDBOX_ORIGIN}
          artefactPath={ARTEFACT_PATH}
          onUpdateModelContext={() => {}}
          hostContext={{ theme: "dark" }}
        />,
      );
    }

    // signalInitialized seeded lastEmittedTheme to "dark"; no redundant push.
    const themeMsgs = cap.captured.filter(
      (m) => (m.msg as { method?: string })?.method === "ui/update-theme",
    );
    expect(themeMsgs).toHaveLength(0);
  });

  it("G35: defaults to 'light' when host omits hostContext entirely", async () => {
    const cap = captureProxyPostMessage();
    const { rerender } = render(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        // No hostContext prop.
      />,
    );
    await waitFor(() => expect(cap.setup()).toBe(true));
    signalInitialized();

    // Now provide a hostContext with theme=dark — must emit dark (no
    // "undefined" / null theme leaks into the notification payload).
    rerender(
      <StaticArtefactFrame
        sandboxOrigin={SANDBOX_ORIGIN}
        artefactPath={ARTEFACT_PATH}
        onUpdateModelContext={() => {}}
        hostContext={{ theme: "dark" }}
      />,
    );

    await waitFor(() => {
      const themeMsg = cap.captured.find(
        (m) => (m.msg as { method?: string })?.method === "ui/update-theme",
      );
      expect(themeMsg).toBeDefined();
      expect((themeMsg!.msg as { params: { theme: string } }).params.theme).toBe("dark");
    });
  });
});
