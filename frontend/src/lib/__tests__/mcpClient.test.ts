// M2A — frontend MCP client (Path A: client → /api/proxy/mcp/{server_id})
// Verifies:
//   * createMcpClient wires StreamableHTTPClientTransport at the proxy URL
//   * the transport's HTTP requests include the Firebase ID token
//     (i.e. fetchWithAuth-equivalent behaviour)
//   * useMcpClient defers connection until a server_id is supplied
//   * useMcpClient caches Client instances per server_id
//
// We mock @modelcontextprotocol/sdk so we can spy on Client + transport
// construction without spinning up real network calls or Firebase.

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---- Mocks ------------------------------------------------------------

vi.mock("@/lib/firebase", () => ({
  getIdToken: vi.fn(async () => "test-id-token"),
}));

const transportCtor = vi.fn();
const clientConnect = vi.fn(async () => undefined);
const clientCtor = vi.fn();

vi.mock("@modelcontextprotocol/sdk/client/streamableHttp.js", () => ({
  StreamableHTTPClientTransport: vi.fn().mockImplementation((url, opts) => {
    transportCtor(url, opts);
    return { __isTransport: true, url, opts };
  }),
}));

vi.mock("@modelcontextprotocol/sdk/client/index.js", () => ({
  Client: vi.fn().mockImplementation((info, opts) => {
    clientCtor(info, opts);
    return {
      __isClient: true,
      info,
      opts,
      connect: clientConnect,
      close: vi.fn(async () => undefined),
    };
  }),
}));

beforeEach(async () => {
  transportCtor.mockClear();
  clientCtor.mockClear();
  clientConnect.mockClear();
  // Clear the module-level Client cache so each test starts fresh.
  const mod = await import("@/lib/mcpClient");
  mod.__resetMcpClientCacheForTests();
});

// ---- Tests ------------------------------------------------------------

describe("createMcpClient", () => {
  it("constructs a transport pointing at /api/proxy/mcp/{server_id}", async () => {
    const { createMcpClient } = await import("@/lib/mcpClient");
    createMcpClient("map-server");
    expect(transportCtor).toHaveBeenCalledOnce();
    const [url] = transportCtor.mock.calls[0];
    expect(url).toBeInstanceOf(URL);
    expect((url as URL).pathname).toBe("/api/proxy/mcp/map-server");
  });

  it("URL-encodes the server_id so weird IDs survive the proxy hop", async () => {
    const { createMcpClient } = await import("@/lib/mcpClient");
    createMcpClient("with space/and-slash");
    const [url] = transportCtor.mock.calls[0];
    expect((url as URL).pathname).toBe(
      "/api/proxy/mcp/with%20space%2Fand-slash",
    );
  });

  it("supplies a custom fetch that attaches the Firebase Authorization header", async () => {
    const { createMcpClient } = await import("@/lib/mcpClient");
    createMcpClient("map-server");
    const [, opts] = transportCtor.mock.calls[0];
    expect(opts).toBeDefined();
    expect(typeof opts.fetch).toBe("function");

    // Spy on global fetch so we can confirm headers flow through.
    const origFetch = global.fetch;
    const fetchSpy = vi.fn(
      async () => new Response(null, { status: 200 }),
    ) as unknown as typeof global.fetch;
    global.fetch = fetchSpy;

    try {
      await opts.fetch("https://example.test/mcp", { method: "POST" });
    } finally {
      global.fetch = origFetch;
    }

    expect(fetchSpy).toHaveBeenCalledOnce();
    const callArgs = (fetchSpy as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0];
    const init = callArgs[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("authorization")).toBe("Bearer test-id-token");
  });

  it("declares the UI extension capability so servers know we render MCP Apps", async () => {
    const { createMcpClient } = await import("@/lib/mcpClient");
    createMcpClient("map-server");
    expect(clientCtor).toHaveBeenCalledOnce();
    const [info, opts] = clientCtor.mock.calls[0];
    expect(info).toMatchObject({ name: expect.any(String), version: expect.any(String) });
    expect(opts.capabilities.extensions).toBeDefined();
    expect(opts.capabilities.extensions["io.modelcontextprotocol/ui"])
      .toBeDefined();
  });
});

describe("useMcpClient", () => {
  it("returns null when serverId is null and never connects", async () => {
    const { useMcpClient } = await import("@/lib/mcpClient");
    const { result } = renderHook(() => useMcpClient(null));
    // Give any async effects a tick to potentially run.
    await new Promise((r) => setTimeout(r, 0));
    expect(result.current).toBeNull();
    expect(clientConnect).not.toHaveBeenCalled();
  });

  it("returns null while connecting, then the Client once ready", async () => {
    const { useMcpClient } = await import("@/lib/mcpClient");
    let resolveConnect!: () => void;
    clientConnect.mockImplementationOnce(
      () =>
        new Promise<undefined>((res) => {
          resolveConnect = () => res(undefined);
        }),
    );

    const { result } = renderHook(() => useMcpClient("map-server"));
    expect(result.current).toBeNull();

    resolveConnect();
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(
      (result.current as unknown as { __isClient: boolean }).__isClient,
    ).toBe(true);
  });

  it("returns the same Client instance for the same serverId (cache hit)", async () => {
    const { useMcpClient } = await import("@/lib/mcpClient");
    const { result: r1 } = renderHook(() => useMcpClient("map-server"));
    const { result: r2 } = renderHook(() => useMcpClient("map-server"));
    await waitFor(() => expect(r1.current).not.toBeNull());
    await waitFor(() => expect(r2.current).not.toBeNull());
    expect(r1.current).toBe(r2.current);
    // Only one Client constructed across both hook invocations
    expect(clientCtor).toHaveBeenCalledOnce();
  });

  it("returns different Client instances for different serverIds", async () => {
    const { useMcpClient } = await import("@/lib/mcpClient");
    const { result: r1 } = renderHook(() => useMcpClient("alpha"));
    const { result: r2 } = renderHook(() => useMcpClient("beta"));
    await waitFor(() => expect(r1.current).not.toBeNull());
    await waitFor(() => expect(r2.current).not.toBeNull());
    expect(r1.current).not.toBe(r2.current);
    expect(clientCtor).toHaveBeenCalledTimes(2);
  });
});
