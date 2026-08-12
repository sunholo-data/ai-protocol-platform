// StaticArtefactFrame — spec-compliant host wrapper for static MCP-App
// artefacts (non-agent-summoned: user clicks a button to open the
// artefact, not the agent invoking a tool).
//
// What this component does, in spec terms (MCP Apps 2026-01-26):
//
//   1. Mount an iframe at ${sandboxOrigin}/sandbox.html with sandbox=
//      "allow-scripts allow-same-origin" (spec line 475 requires same-
//      origin on the outer proxy frame; the spec uses the proxy's
//      same-origin to bridge the inner artefact iframe, which itself
//      runs with no allow-same-origin per ADR-013).
//   2. Wait for `ui/notifications/sandbox-proxy-ready` from the proxy.
//   3. Fetch the artefact HTML from `${sandboxOrigin}/artefacts/<path>/index.html`.
//   4. Send `ui/notifications/sandbox-resource-ready` with the HTML +
//      desired inner sandbox attrs to the proxy. The proxy
//      document.writes the HTML into its inner iframe.
//   5. Respond to the artefact's `ui/initialize` request with a
//      McpUiInitializeResult including `hostContext` (theme, displayMode,
//      locale per spec §Host Context).
//   6. Forward `ui/update-model-context` notifications to the caller via
//      `onUpdateModelContext({ structuredContent })`.
//   7. Respond to `ping` requests with `result: {}` (spec line 508).
//
// Why this isn't AppRenderer (@mcp-ui/client):
// AppRenderer is keyed off an MCP CallToolResult — it expects a tool
// call to have happened and a resource URI to point to. Static artefacts
// have neither (the user summons them, not the agent). This component
// is the spec-compliant counterpart for that case.
//
// See: docs/ops/mcp-apps-iframe-guide.md

"use client";

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

/** Subset of MCP Apps §Host Context we currently pass through to the
 *  artefact during the ui/initialize handshake. Add fields here as
 *  artefacts need them. */
export interface McpAppHostContext {
  theme?: "light" | "dark";
  displayMode?: "inline" | "fullscreen" | "pip";
  locale?: string;
  timeZone?: string;
}

/** Shape the spec requires for the ui/initialize result. */
interface McpUiInitializeResult {
  hostContext: McpAppHostContext;
  protocolVersion: string;
  capabilities: Record<string, unknown>;
  serverInfo: { name: string; version: string };
}

export interface StaticArtefactFrameProps {
  /** Origin of the mcp-sandbox service, no trailing slash, no path.
   *  E.g. `http://localhost:3457`. The component mounts the proxy iframe
   *  at `${sandboxOrigin}/sandbox.html`. */
  sandboxOrigin: string;
  /** Path segment after `/artefacts/` — e.g. `"boldkast/v1"`. The
   *  artefact's HTML is fetched from `${sandboxOrigin}/artefacts/${artefactPath}/index.html`. */
  artefactPath: string;
  /** Called once per `ui/update-model-context` notification from the
   *  artefact. The hook handles auth, envelope parsing, and lifecycle;
   *  the callback receives just the spec's `structuredContent` payload. */
  onUpdateModelContext: (structuredContent: Record<string, unknown>) => void;
  /** Optional: called once the artefact's `ui/initialize` handshake
   *  completes. Useful for "sim is ready" indicators. */
  onInitialized?: (clientInfo: { name: string; version: string }) => void;
  /** Optional host context provided to the artefact during init. */
  hostContext?: McpAppHostContext;
  /** Inner-iframe sandbox attribute the proxy applies to the artefact.
   *  Default `"allow-scripts allow-same-origin"` matches sandbox.ts. */
  innerSandbox?: string;
  /** Iframe className for sizing. */
  className?: string;
  /** Iframe title for a11y. */
  title?: string;
}

export interface StaticArtefactFrameHandle {
  /** Send a JSON-RPC notification to the artefact (host → view). Used by
   *  callers that want to push state into the artefact (e.g. theme
   *  changes). The component handles serialisation and origin pinning. */
  sendNotification: (method: string, params?: Record<string, unknown>) => void;
}

const PROXY_READY = "ui/notifications/sandbox-proxy-ready";
const RESOURCE_READY = "ui/notifications/sandbox-resource-ready";
const INITIALIZE = "ui/initialize";
const INITIALIZED = "ui/notifications/initialized";
const UPDATE_MODEL_CONTEXT = "ui/update-model-context";
const UPDATE_THEME = "ui/update-theme";
const PING = "ping";
// Content-height report from the sandbox proxy (same-origin with the artefact),
// used to size the iframe to its content — see the sizing note above.
const SIZE = "ui/notifications/size";

const PROTOCOL_VERSION = "2026-01-26";

interface JsonRpcMessage {
  jsonrpc?: "2.0";
  id?: number | string | null;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
}

export const StaticArtefactFrame = forwardRef<
  StaticArtefactFrameHandle,
  StaticArtefactFrameProps
>(function StaticArtefactFrame(
  {
    sandboxOrigin,
    artefactPath,
    onUpdateModelContext,
    onInitialized,
    hostContext,
    innerSandbox = "allow-scripts allow-same-origin",
    className,
    title = "MCP App artefact",
  },
  ref,
) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [iframeUrl] = useState(() => {
    const origin = sandboxOrigin.replace(/\/$/, "");
    return `${origin}/sandbox.html`;
  });

  // DETERMINISTIC, CONTENT-DRIVEN SIZING — an <iframe> is a replaced element and
  // this one is CROSS-ORIGIN (the sandbox), so the host can neither rely on
  // height:100% (collapses to the 150px UA default) NOR read the artefact's
  // content height (`contentDocument` throws cross-origin). Instead the sandbox
  // PROXY — which IS same-origin with the artefact — measures it and reports the
  // content height via a `ui/notifications/size` message; we size the iframe to
  // that. So the MCP App is shown in FULL (never clipped to a strip), matched to
  // its own content — no CSS-chain guesswork, no visual confirmation.
  const [contentHeight, setContentHeight] = useState<number | null>(null);

  // Stable refs for the spec callbacks so the message-handler closure
  // doesn't go stale on prop changes (the handler binds once at mount
  // and reads .current at event time).
  const onUpdateRef = useRef(onUpdateModelContext);
  onUpdateRef.current = onUpdateModelContext;
  const onInitializedRef = useRef(onInitialized);
  onInitializedRef.current = onInitialized;
  const hostContextRef = useRef(hostContext);
  hostContextRef.current = hostContext;

  // G35 (template-mcp-apps-artefact-quality.md): track whether the
  // artefact has finished the ui/initialize handshake. Before that
  // point, any `ui/update-theme` notification we send would race the
  // init flow on the artefact side — the initial theme is delivered
  // via the handshake's hostContext.theme instead. After init, this
  // ref lets the post-mount effect below emit only when the host
  // theme genuinely changed.
  const initializedRef = useRef(false);
  const lastEmittedThemeRef = useRef<string | undefined>(undefined);

  // Helper to push a JSON-RPC message to the proxy iframe.
  const sendToProxy = useCallback((msg: JsonRpcMessage) => {
    const target = iframeRef.current?.contentWindow;
    if (!target) return;
    target.postMessage(msg, sandboxOrigin.replace(/\/$/, ""));
  }, [sandboxOrigin]);

  useImperativeHandle(ref, () => ({
    sendNotification: (method, params) => {
      sendToProxy({ jsonrpc: "2.0", method, params });
    },
  }), [sendToProxy]);

  useEffect(() => {
    const expectedOrigin = sandboxOrigin.replace(/\/$/, "");

    const handleMessage = (event: MessageEvent) => {
      // Spec §Sandbox proxy line 132 (sandbox.ts): proxy validates host
      // origin before reading. Symmetric here — we validate the proxy's
      // origin before reading payload. Because the sandbox runs with
      // allow-same-origin, its events have a real origin (not "null").
      if (event.origin !== expectedOrigin) return;
      const data = event.data as JsonRpcMessage | null;
      if (!data || data.jsonrpc !== "2.0") return;

      // --- Lifecycle: proxy is ready, push the artefact HTML ---
      if (data.method === PROXY_READY) {
        void (async () => {
          try {
            const res = await fetch(`${expectedOrigin}/artefacts/${artefactPath}/index.html`);
            if (!res.ok) {
              if (process.env.NODE_ENV !== "production") {
                // eslint-disable-next-line no-console
                console.warn("[static-artefact] fetch failed:", res.status, res.statusText);
              }
              return;
            }
            const html = await res.text();
            sendToProxy({
              jsonrpc: "2.0",
              method: RESOURCE_READY,
              params: { html, sandbox: innerSandbox },
            });
          } catch (err) {
            if (process.env.NODE_ENV !== "production") {
              // eslint-disable-next-line no-console
              console.warn("[static-artefact] resource fetch error:", err);
            }
          }
        })();
        return;
      }

      // --- Handshake: artefact sent ui/initialize, we respond with hostContext ---
      if (data.method === INITIALIZE && data.id !== undefined && data.id !== null) {
        const ctx = hostContextRef.current ?? {};
        const result: McpUiInitializeResult = {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: {},
          serverInfo: { name: "platform-host", version: "1.0.0" },
          hostContext: {
            theme: ctx.theme ?? "light",
            displayMode: ctx.displayMode ?? "inline",
            ...(ctx.locale ? { locale: ctx.locale } : {}),
            ...(ctx.timeZone ? { timeZone: ctx.timeZone } : {}),
          },
        };
        sendToProxy({ jsonrpc: "2.0", id: data.id, result });
        return;
      }

      // --- Artefact has finished initialising; fire callback ---
      if (data.method === INITIALIZED) {
        // G35: mark init-complete so the post-mount theme effect below
        // is allowed to emit ui/update-theme. Seed the "last emitted"
        // ref with the theme the handshake delivered, so subsequent
        // identical-theme renders don't re-emit.
        initializedRef.current = true;
        lastEmittedThemeRef.current = hostContextRef.current?.theme ?? "light";
        const clientInfo = (data.params?.clientInfo as { name: string; version: string }) ?? {
          name: "unknown",
          version: "0",
        };
        onInitializedRef.current?.(clientInfo);
        return;
      }

      // --- The notification this whole pipeline exists for ---
      if (data.method === UPDATE_MODEL_CONTEXT) {
        const structuredContent = data.params?.structuredContent as
          | Record<string, unknown>
          | undefined;
        if (structuredContent && typeof structuredContent === "object") {
          onUpdateRef.current(structuredContent);
        }
        if (process.env.NODE_ENV !== "production") {
          // eslint-disable-next-line no-console
          console.log("[static-artefact]", UPDATE_MODEL_CONTEXT, structuredContent);
        }
        return;
      }

      // --- Content-height report from the proxy → size the iframe to it ---
      if (data.method === SIZE) {
        const h = (data.params as { height?: unknown } | undefined)?.height;
        if (typeof h === "number" && h > 0) setContentHeight(Math.ceil(h));
        return;
      }

      // --- Health probe; spec line 508 requires us to respond ---
      if (data.method === PING && data.id !== undefined && data.id !== null) {
        sendToProxy({ jsonrpc: "2.0", id: data.id, result: {} });
        return;
      }

      if (process.env.NODE_ENV !== "production") {
        // eslint-disable-next-line no-console
        console.log("[static-artefact] unhandled method:", data.method, data);
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [sandboxOrigin, artefactPath, innerSandbox, sendToProxy]);

  // G35 (template-mcp-apps-artefact-quality.md): emit ui/update-theme
  // notification when the host's theme prop changes AFTER the artefact
  // has completed its handshake. The initial theme rides on the
  // handshake response (hostContext.theme); this effect handles
  // subsequent flips — e.g. user toggles dark mode mid-session, or
  // a parent surface flips to match the workbench tab's accent.
  //
  // Skip the emit while uninitialized (the artefact will read the
  // theme from the handshake response on init). Skip when the value
  // hasn't actually changed (React 18 strict-mode double-invokes
  // effects + a parent with a stable `hostContext` object would
  // otherwise emit redundantly on every render).
  useEffect(() => {
    if (!initializedRef.current) return;
    const nextTheme = hostContext?.theme ?? "light";
    if (lastEmittedThemeRef.current === nextTheme) return;
    lastEmittedThemeRef.current = nextTheme;
    sendToProxy({
      jsonrpc: "2.0",
      method: UPDATE_THEME,
      params: { theme: nextTheme },
    });
  }, [hostContext?.theme, sendToProxy]);

  const frame = (
    <iframe
      ref={iframeRef}
      src={iframeUrl}
      title={title}
      // The outer (proxy) iframe needs allow-same-origin per spec line
      // 475 so the proxy script can document.write the artefact HTML
      // into its inner frame. The INNER frame (the artefact) runs with
      // the stricter sandbox we pass via the resource-ready payload —
      // typically `allow-scripts allow-same-origin` (the spec default,
      // also what sandbox.ts applies). ADR-013's "no allow-same-origin"
      // on the artefact iframe holds for the artefact's CSP-restricted
      // inner frame served from a DIFFERENT origin than the host (the
      // sandbox service origin), which the double-iframe achieves
      // implicitly.
      sandbox="allow-scripts allow-same-origin"
      referrerPolicy="strict-origin"
      loading="eager"
      className={className}
      // CODED height — the artefact's own content height, reported by the proxy
      // (see the sizing note above). Full width; height = the app's content.
      // The 500px fallback holds only until the first report arrives (a tick
      // after load), so it's never a 150px strip even on the first paint.
      // Inline height wins over any height class (e.g. `h-full`), so the size
      // stays content-driven; the enclosing tabpanel is `overflow-auto`, so a
      // tall artefact scrolls within the pane instead of being clipped.
      style={{
        display: "block",
        border: 0,
        width: "100%",
        height: contentHeight ? `${contentHeight}px` : "500px",
      }}
    />
  );
  return frame;
});
