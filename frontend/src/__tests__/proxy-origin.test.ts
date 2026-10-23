// @vitest-environment node
//
// Catch-all proxy — the browser's Origin header must not reach the sidecar.
//
// google-adk >= 1.39 (the sidecar) answers 403 "origin not allowed" to any
// request whose Origin it does not recognise, and the browser's origin
// (https://app.example.com) never matches the sidecar's own 127.0.0.1. Every
// browser write broke on test after the v6.54.0 bump (2026-10-20) while CLI
// checks, which send no Origin, passed.

import http from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

let server: http.Server;
let lastSeenHeaders: http.IncomingHttpHeaders = {};
let requests = 0;

beforeAll(async () => {
  server = http.createServer((req, res) => {
    requests += 1;
    lastSeenHeaders = req.headers;
    req.resume();
    req.on("end", () => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  process.env.BACKEND_URL = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterEach(() => { vi.unstubAllEnvs(); });

afterAll(async () => {
  delete process.env.BACKEND_URL;
  await new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
});

describe("proxy — Origin", () => {
  it("drops the browser's Origin on a write and keeps the Bearer token", async () => {
    vi.resetModules();
    const route = await import("@/app/api/proxy/[...path]/route");
    const url = "http://localhost:3000/api/proxy/api/projects";
    const req = new Request(url, {
      method: "POST",
      headers: {
        Origin: "https://app.example.com",
        Authorization: "Bearer t0ken",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: "Iberia" }),
      // @ts-expect-error -- undici needs duplex for a streamed body
      duplex: "half",
    }) as Request & { nextUrl: URL };
    req.nextUrl = new URL(url);

    const res = await route.POST(req as never, { params: Promise.resolve({ path: ["api", "projects"] }) });

    expect(res.status).toBe(200);
    expect(lastSeenHeaders.origin).toBeUndefined();
    expect(lastSeenHeaders.authorization).toBe("Bearer t0ken");
  });

  it.each<Record<string, string>>([
    { Origin: "https://attacker.example" },
    { Origin: "null" },
    { Origin: "invalid" },
    { "Sec-Fetch-Site": "cross-site" },
    { Origin: "https://app.example.com:444" },
  ])("rejects an unsafe tailnet browser write before forwarding: %j", async (headers) => {
    vi.stubEnv("TAILNET_PROXY_SECRET", "test-proxy-secret");
    vi.resetModules();
    const route = await import("@/app/api/proxy/[...path]/route");
    const url = "http://localhost:3000/api/proxy/api/projects";
    const req = new Request(url, {
      method: "POST",
      headers: { Host: "app.example.com", ...headers },
    }) as Request & { nextUrl: URL };
    req.nextUrl = new URL(url);
    const before = requests;
    const res = await route.POST(req as never, { params: Promise.resolve({ path: ["api", "projects"] }) });
    expect(res.status).toBe(403);
    expect(requests).toBe(before);
  });

  it.each([false, true])("accepts tailnet writes from the public host or native clients (browser=%s)", async (browser) => {
    vi.stubEnv("TAILNET_PROXY_SECRET", "test-proxy-secret");
    vi.resetModules();
    const route = await import("@/app/api/proxy/[...path]/route");
    const url = "http://localhost:3000/api/proxy/api/projects";
    const headers = new Headers({ Host: "localhost:3000", "X-Forwarded-Host": "app.example.com" });
    if (browser) {
      headers.set("Origin", "https://app.example.com");
      headers.set("Sec-Fetch-Site", "same-origin");
    }
    const req = new Request(url, { method: "POST", headers }) as Request & { nextUrl: URL };
    req.nextUrl = new URL(url);
    const res = await route.POST(req as never, { params: Promise.resolve({ path: ["api", "projects"] }) });
    expect(res.status).toBe(200);
    expect(lastSeenHeaders.origin).toBeUndefined();
    expect(lastSeenHeaders["x-proxy-secret"]).toBe("test-proxy-secret");
  });
});
