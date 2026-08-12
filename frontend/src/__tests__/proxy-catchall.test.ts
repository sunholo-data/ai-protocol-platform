// @vitest-environment node
//
// Catch-all proxy route — regression suite.
//
// MODEL-RELIABILITY M1 rework: the route no longer uses fetch() (undici's
// bodyTimeout killed long SSE streams — the v5 long-stream incident), so the old
// fetch-mocking approach can't intercept anything. These tests now run the
// route handler against a REAL local http server, which is strictly better:
// every original regression intent (auth forwarding, 401 passthrough,
// 204 null-body, 502 unreachable, host stripping, SSE incremental streaming,
// non-SSE buffering) is preserved, and the actual node:http transport is
// exercised instead of a mock's assumptions.

import http from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

type RouteModule = typeof import("@/app/api/proxy/[...path]/route");

let server: http.Server;
let route: RouteModule;
/** Headers as received by the upstream for the last /echo-headers request. */
let lastSeenHeaders: http.IncomingHttpHeaders = {};

function makeReq(path: string, init: RequestInit & { url?: string } = {}) {
  const url = init.url ?? `http://localhost:3000/api/proxy/${path}`;
  const req = new Request(url, init) as Request & { nextUrl: URL };
  req.nextUrl = new URL(url);
  return req;
}

beforeAll(async () => {
  server = http.createServer((req, res) => {
    if (req.url === "/api/skills") {
      if (!req.headers.authorization) {
        res.writeHead(401, { "content-type": "application/json" });
        res.end(JSON.stringify({ detail: "Missing Authorization header" }));
        return;
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ skills: [] }));
      return;
    }
    if (req.url === "/echo-headers") {
      lastSeenHeaders = req.headers;
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    if (req.url === "/api/sessions/sess-1/iframe-context") {
      // Regression: sprint 1.25's endpoint returns 204 No Content — the Web
      // Response constructor forbids a body on 204, and mishandling that
      // used to surface as a spurious 502 (found in deployed-dev E2E,
      // 2026-05-01).
      res.writeHead(204);
      res.end();
      return;
    }
    if (req.url === "/api/skill/test-skill/stream") {
      res.writeHead(200, { "content-type": "text/event-stream" });
      const chunks = [
        'data: {"type":"TEXT_MESSAGE_CHUNK","delta":"tok1"}\n\n',
        'data: {"type":"TEXT_MESSAGE_CHUNK","delta":"tok2"}\n\n',
        'data: {"type":"RUN_FINISHED"}\n\n',
      ];
      let i = 0;
      const timer = setInterval(() => {
        res.write(chunks[i]);
        i += 1;
        if (i === chunks.length) {
          clearInterval(timer);
          res.end();
        }
      }, 25);
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not_found" }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;

  // BACKEND_URL is bound at module import — set it before importing the route.
  vi.resetModules();
  process.env.BACKEND_URL = `http://127.0.0.1:${port}`;
  route = await import("@/app/api/proxy/[...path]/route");
});

afterAll(async () => {
  delete process.env.BACKEND_URL;
  await new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
});

describe("catch-all proxy route (real node:http upstream)", () => {
  it("forwards Authorization: Bearer to the backend", async () => {
    const req = makeReq("echo-headers", {
      method: "GET",
      headers: { Authorization: "Bearer test-token" },
    });
    const res = await route.GET(req as never, {
      params: Promise.resolve({ path: ["echo-headers"] }),
    });
    expect(res.status).toBe(200);
    expect(lastSeenHeaders.authorization).toBe("Bearer test-token");
  });

  it("returns the backend 401 as-is (does not shadow to Next 404)", async () => {
    const req = makeReq("api/skills", { method: "GET" });
    const res = await route.GET(req as never, {
      params: Promise.resolve({ path: ["api", "skills"] }),
    });
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Missing Authorization header" });
  });

  it("forwards a 204 No Content as 204 (not 502 — null-body status)", async () => {
    const req = makeReq("api/sessions/sess-1/iframe-context", {
      method: "POST",
      headers: { Authorization: "Bearer t", "content-type": "application/json" },
      body: JSON.stringify({ serverId: "ext-apps-map" }),
    });
    const res = await route.POST(req as never, {
      params: Promise.resolve({ path: ["api", "sessions", "sess-1", "iframe-context"] }),
    });
    expect(res.status).toBe(204);
    expect(res.body).toBeNull();
  });

  it("returns 502 when the backend is unreachable", async () => {
    // Fresh route module bound to a dead port.
    vi.resetModules();
    const prev = process.env.BACKEND_URL;
    process.env.BACKEND_URL = "http://127.0.0.1:9";
    const deadRoute: RouteModule = await import("@/app/api/proxy/[...path]/route");
    process.env.BACKEND_URL = prev;

    const req = makeReq("api/skills", { method: "GET" });
    const res = await deadRoute.GET(req as never, {
      params: Promise.resolve({ path: ["api", "skills"] }),
    });
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBe("backend_unreachable");
  });

  it("strips the client Host header before forwarding", async () => {
    const req = makeReq("echo-headers", {
      method: "GET",
      headers: { host: "example.com", Authorization: "Bearer t" },
    });
    await route.GET(req as never, {
      params: Promise.resolve({ path: ["echo-headers"] }),
    });
    // node:http sets its own Host (the upstream target) — the client's
    // spoofable host must not leak through.
    expect(lastSeenHeaders.host).not.toBe("example.com");
    expect(lastSeenHeaders.authorization).toBe("Bearer t");
  });

  describe("SSE streaming", () => {
    it("pipes text/event-stream responses as a ReadableStream, chunks arriving incrementally", async () => {
      const req = makeReq("api/skill/test-skill/stream", { method: "POST" });
      const res = await route.POST(req as never, {
        params: Promise.resolve({ path: ["api", "skill", "test-skill", "stream"] }),
      });

      expect(res.status).toBe(200);
      expect(res.headers.get("content-type")).toContain("text/event-stream");
      expect(res.body).toBeInstanceOf(ReadableStream);

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      const received: string[] = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received.push(decoder.decode(value, { stream: true }));
      }
      // Upstream writes 3 chunks 25ms apart — a buffering proxy would deliver
      // one blob; the streaming path delivers multiple reads.
      expect(received.length).toBeGreaterThanOrEqual(2);
      const full = received.join("");
      expect(full).toContain("tok1");
      expect(full).toContain("tok2");
      expect(full).toContain("RUN_FINISHED");
    });

    it("buffers non-SSE responses normally (regression guard)", async () => {
      const req = makeReq("api/skills", {
        method: "GET",
        headers: { Authorization: "Bearer t" },
      });
      const res = await route.GET(req as never, {
        params: Promise.resolve({ path: ["api", "skills"] }),
      });
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ skills: [] });
    });
  });
});
