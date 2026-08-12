// useMcpAppMessages — spec-compliant message listener for MCP-App
// iframe artefacts going through the sandbox proxy.
//
// Auth via `e.origin === sandboxOrigin` (the proxy has a real origin
// per MCP Apps spec §Sandbox proxy lines 470–487). Wire format:
// JSON-RPC 2.0 envelopes discriminated by the `method` field.
//
// Pair with `<StaticArtefactFrame>` for the full host-side spec
// compliance story (handshake, lifecycle, ping, origin auth). This
// hook is the listener primitive both the frame component and any
// downstream artefact wrapper use — and is also useful standalone
// for telemetry / dev pages / tests that want to observe iframe
// notifications outside the frame.
//
// See: docs/ops/mcp-apps-iframe-guide.md

"use client";

import { useEffect } from "react";

/** Minimum shape of an incoming JSON-RPC notification we'll forward. */
export interface JsonRpcNotification<TParams = Record<string, unknown>> {
  jsonrpc: "2.0";
  method: string;
  params?: TParams;
}

export interface UseMcpAppMessagesOptions<TParams = Record<string, unknown>> {
  /** Origin to validate against. Events with `e.origin !== sandboxOrigin`
   *  are rejected. The sandbox proxy has a real origin (different from
   *  the host's, per spec line 474), so origin-based auth works here. */
  sandboxOrigin: string;
  /** Which JSON-RPC method to listen for. E.g. `"ui/update-model-context"`. */
  method: string;
  /** Called once per valid notification matching `method`. The hook
   *  handles auth, envelope parsing, and lifecycle. */
  onNotification: (params: TParams) => void;
  /** Optional dev-mode console label. Defaults to the method name. */
  debugLabel?: string;
}

/**
 * Register a window message listener that accepts spec-compliant JSON-RPC
 * notifications from the MCP-App sandbox proxy, validates the method,
 * and forwards the params to the caller. Auth is by origin (matches the
 * spec's recommended approach for the sandbox-proxy pattern).
 *
 * Pair with `<StaticArtefactFrame>` (which handles the handshake +
 * lifecycle) for the full host-side spec compliance story. This hook is
 * useful when you want to observe notifications outside the frame
 * component (e.g. for telemetry, dev pages, or tests).
 */
export function useMcpAppMessages<TParams = Record<string, unknown>>(
  opts: UseMcpAppMessagesOptions<TParams>,
): void {
  const { sandboxOrigin, method, onNotification, debugLabel } = opts;
  const label = debugLabel || method;

  useEffect(() => {
    const expectedOrigin = sandboxOrigin.replace(/\/$/, "");

    const handler = (e: MessageEvent) => {
      // Spec-compliant auth: the sandbox proxy has a real origin
      // (different from the host's per spec line 474), so origin-based
      // auth works. This is the spec's intended pattern.
      if (e.origin !== expectedOrigin) return;
      const data = e.data as JsonRpcNotification<TParams> | null;
      if (!data || data.jsonrpc !== "2.0") return;
      if (data.method !== method) return;
      const params = (data.params ?? ({} as TParams)) as TParams;
      onNotification(params);
      if (process.env.NODE_ENV !== "production") {
        // eslint-disable-next-line no-console
        console.log(`[${label}]`, params);
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
    // onNotification is intentionally omitted from deps: rebinding the
    // window listener on every render would lose events fired during
    // the swap. Callers should pass a stable handler (useCallback or
    // a ref inside their component).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sandboxOrigin, method, label]);
}
