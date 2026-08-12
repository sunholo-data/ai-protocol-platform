// M2A — frontend MCP client (Path A: client → /api/proxy/mcp/{server_id})
//
// We instantiate a `Client` from the MCP TypeScript SDK pointed at our backend
// proxy. The proxy (M2B) forwards JSON-RPC traffic to the registered MCP
// server using the per-server config in Firestore. From the frontend's
// perspective the proxy IS the MCP server — same Streamable HTTP transport,
// just a different URL.
//
// Auth: every request to `/api/proxy/*` MUST carry the Firebase ID token. The
// SDK transport accepts a `fetch` override, which we use to wrap fetch with
// the Authorization header (mirroring `fetchWithAuth` from `lib/apiClient`).
// We can't pass `fetchWithAuth` directly because the SDK expects the bare
// `fetch(input, init)` signature — but the behaviour is identical.
//
// We declare the MCP UI extension capability so servers know we render
// `text/html;profile=mcp-app` resources (per SEP-1724 / @mcp-ui/client).
//
// Caching: a single Client per `server_id`, lifetime = browser tab. React
// re-renders should never reconnect; opening a new tab does. The cache
// lives at module scope so all components in the tree see the same Client.

"use client";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import type { ClientCapabilities } from "@modelcontextprotocol/sdk/types.js";
import { TRANSPORT_FIELD } from "@/lib/branding";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { UI_EXTENSION_CAPABILITIES } from "@mcp-ui/client";
import { useEffect, useState } from "react";
import { getIdToken } from "@/lib/firebase";

const CLIENT_INFO = { name: "platform", version: "0.1.0" } as const;

// SDK's ClientCapabilities type narrows `extensions` to `{[k]: object}` —
// our UI extension declaration matches that shape, but the @mcp-ui/client
// helper types its values as `unknown`. Cast at the boundary so the SDK
// accepts it without widening the SDK's typed surface.
const CAPABILITIES: ClientCapabilities = {
  extensions: UI_EXTENSION_CAPABILITIES as unknown as {
    [name: string]: object;
  },
};

/** Wrap fetch so every request to the MCP proxy carries the Firebase ID
 * token in the Authorization header — same shape as `fetchWithAuth`. */
async function authedFetch(
  input: RequestInfo | URL | string,
  init: RequestInit = {},
): Promise<Response> {
  const token = await getIdToken();
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(input as RequestInfo | URL, { ...init, headers });
}

/**
 * Build a (disconnected) MCP `Client` for the given server_id, wired to the
 * backend proxy URL `/api/proxy/mcp/{server_id}` and the UI extension
 * capability. Caller is responsible for `client.connect(transport)` —
 * `useMcpClient` does that for components.
 */
export function createMcpClient(serverId: string): Client {
  const url = new URL(
    `/api/proxy/mcp/${encodeURIComponent(serverId)}`,
    // jsdom + Node both expose window.location.origin during tests; if not
    // present (SSR / older Node) URL still constructs against a relative path
    // when no base is given, but we want an absolute URL so the SDK doesn't
    // hit the standard same-origin policy quirks. Use a benign placeholder
    // origin in non-browser contexts.
    typeof window !== "undefined" ? window.location.origin : "http://localhost",
  );

  const transport = new StreamableHTTPClientTransport(url, {
    fetch: authedFetch,
  });

  const client = new Client(CLIENT_INFO, { capabilities: CAPABILITIES });
  // Attach the transport for callers that bypass the hook. We intentionally
  // do NOT await connect() here — connecting is async and the hook handles
  // the lifecycle; callers using createMcpClient directly must connect.
  // Storing the transport on the client lets `useMcpClient` retrieve it.
  // Property name comes from `branding.TRANSPORT_FIELD` so a fork can
  // rebrand the prefix (`__myappTransport`) without touching this file
  // (#11 — template-fork-ergonomics.md).
  (client as unknown as Record<string, unknown>)[TRANSPORT_FIELD] = transport;
  return client;
}

// Module-level cache. Map<serverId, { client, ready: boolean, promise }>.
interface CacheEntry {
  client: Client;
  ready: boolean;
  promise: Promise<void>;
  listeners: Set<() => void>;
}

const cache = new Map<string, CacheEntry>();

function getOrCreateEntry(serverId: string): CacheEntry {
  const existing = cache.get(serverId);
  if (existing) return existing;

  const client = createMcpClient(serverId);
  const transport = (client as unknown as Record<string, unknown>)[TRANSPORT_FIELD] as Parameters<
    Client["connect"]
  >[0];

  const entry: CacheEntry = {
    client,
    ready: false,
    promise: client.connect(transport).then(
      () => {
        entry.ready = true;
        for (const cb of entry.listeners) cb();
      },
      (err: unknown) => {
        console.warn("mcpClient: connect failed for", serverId, err);
        // Drop from cache on failure so a future render can retry.
        cache.delete(serverId);
        for (const cb of entry.listeners) cb();
      },
    ),
    listeners: new Set(),
  };
  cache.set(serverId, entry);
  return entry;
}

/**
 * React hook: returns the connected `Client` for the given server_id, or
 * `null` while connecting / when serverId is null. Cached per server_id at
 * module scope so re-renders don't reconnect.
 */
export function useMcpClient(serverId: string | null): Client | null {
  const [, force] = useState(0);

  useEffect(() => {
    if (!serverId) return;
    const entry = getOrCreateEntry(serverId);
    if (entry.ready) return; // already connected; no work
    const onReady = () => force((n) => n + 1);
    entry.listeners.add(onReady);
    return () => {
      entry.listeners.delete(onReady);
    };
  }, [serverId]);

  if (!serverId) return null;
  const entry = cache.get(serverId);
  return entry?.ready ? entry.client : null;
}

/** Dev-only: create a Client that connects directly to `serverUrl` without
 * going through the auth proxy. Only used by /dev/* pages to exercise
 * AppRenderer without requiring a Firebase login. Not for production. */
export function createDevDirectMcpClient(serverUrl: string): Client {
  const url = new URL(serverUrl);
  const transport = new StreamableHTTPClientTransport(url);
  const client = new Client(CLIENT_INFO, { capabilities: CAPABILITIES });
  (client as unknown as Record<string, unknown>)[TRANSPORT_FIELD] = transport;
  return client;
}

/** Test-only: clear the module-level Client cache. Lets isolated tests
 * exercise fresh connect lifecycles without cross-tail. */
export function __resetMcpClientCacheForTests(): void {
  cache.clear();
}
