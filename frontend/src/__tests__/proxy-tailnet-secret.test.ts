// @vitest-environment node
//
// Catch-all proxy — tailnet auth mode (backend auth/tailnet_auth.py).
//
// The backend trusts Tailscale-User-Login only when X-Proxy-Secret matches, so
// the proxy must (a) attach TAILNET_PROXY_SECRET when configured, (b) never
// forward a client-supplied X-Proxy-Secret, configured or not, and (c) pass the
// Tailscale identity headers from `tailscale serve` through untouched.

import http from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

type RouteModule = typeof import("@/app/api/proxy/[...path]/route");

let server: http.Server;
let lastSeenHeaders: http.IncomingHttpHeaders = {};

function makeReq(headers: Record<string, string>) {
  const url = "http://localhost:3000/api/proxy/echo-headers";
  const req = new Request(url, { method: "GET", headers }) as Request & { nextUrl: URL };
  req.nextUrl = new URL(url);
  return req;
}

async function importRoute(secret: string | undefined): Promise<RouteModule> {
  // Both env vars are bound at module import.
  vi.resetModules();
  if (secret === undefined) delete process.env.TAILNET_PROXY_SECRET;
  else process.env.TAILNET_PROXY_SECRET = secret;
  return import("@/app/api/proxy/[...path]/route");
}

async function send(route: RouteModule, headers: Record<string, string>) {
  const res = await route.GET(makeReq(headers) as never, {
    params: Promise.resolve({ path: ["echo-headers"] }),
  });
  expect(res.status).toBe(200);
  return lastSeenHeaders;
}

beforeAll(async () => {
  server = http.createServer((req, res) => {
    lastSeenHeaders = req.headers;
    res.writeHead(200, { "content-type": "application/json" });
    res.end("{}");
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  process.env.BACKEND_URL = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  delete process.env.BACKEND_URL;
  delete process.env.TAILNET_PROXY_SECRET;
  await new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
});

describe("proxy — tailnet secret", () => {
  it("attaches the configured secret and passes the Tailscale login through", async () => {
    const route = await importRoute("proxy-secret-for-tests-0123456789abcdef");
    const seen = await send(route, { "Tailscale-User-Login": "ada@example.com" });
    expect(seen["x-proxy-secret"]).toBe("proxy-secret-for-tests-0123456789abcdef");
    expect(seen["tailscale-user-login"]).toBe("ada@example.com");
  });

  it("replaces a client-supplied secret with its own", async () => {
    const route = await importRoute("proxy-secret-for-tests-0123456789abcdef");
    const seen = await send(route, { "X-Proxy-Secret": "forged" });
    expect(seen["x-proxy-secret"]).toBe("proxy-secret-for-tests-0123456789abcdef");
  });

  it("drops a client-supplied secret when none is configured", async () => {
    const route = await importRoute(undefined);
    const seen = await send(route, { "X-Proxy-Secret": "forged" });
    expect(seen["x-proxy-secret"]).toBeUndefined();
  });
});
