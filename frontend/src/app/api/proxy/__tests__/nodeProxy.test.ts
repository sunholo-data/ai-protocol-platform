// @vitest-environment node
//
// MODEL-RELIABILITY M1 — the proxy must not run SSE bodies through undici.
//
// undici's fetch() enforces a bodyTimeout (300s default, semantics vary by
// Node version) that killed a healthy 5-minute Anthropic stream in the v5
// "long-stream incident" (UND_ERR_BODY_TIMEOUT at exactly 300s despite keep-alive
// pings). node:http has no body timeout by design, so the proxy routes ALL
// upstream requests through it: SSE responses stream through chunk-by-chunk,
// everything else buffers exactly as before. These tests run against a real
// local http server — the failure class is transport behavior, and mocks
// would just encode our assumptions.

import http from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { proxyViaNodeHttp } from "../nodeProxy";

let server: http.Server;
let baseUrl: string;

/** Test upstream:
 *  - GET  /sse    → 3 SSE events, 30ms apart, then close
 *  - GET  /json   → JSON body + custom header
 *  - POST /echo   → echoes request body + content-type back
 *  - GET  /nobody → 204 (null-body status)
 */
beforeAll(async () => {
  server = http.createServer((req, res) => {
    if (req.url === "/sse") {
      res.writeHead(200, { "content-type": "text/event-stream" });
      let n = 0;
      const timer = setInterval(() => {
        n += 1;
        res.write(`data: {"n":${n}}\n\n`);
        if (n === 3) {
          clearInterval(timer);
          res.end();
        }
      }, 30);
      return;
    }
    if (req.url === "/json") {
      res.writeHead(200, { "content-type": "application/json", "x-upstream": "yes" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    if (req.url === "/echo") {
      const chunks: Buffer[] = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        res.writeHead(200, { "content-type": req.headers["content-type"] ?? "application/octet-stream" });
        res.end(Buffer.concat(chunks));
      });
      return;
    }
    if (req.url === "/nobody") {
      res.writeHead(204);
      res.end();
      return;
    }
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not_found" }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve())));
});

describe("proxyViaNodeHttp", () => {
  it("streams SSE responses chunk-by-chunk (not buffered until close)", async () => {
    const res = await proxyViaNodeHttp(`${baseUrl}/sse`, { method: "GET", headers: new Headers() });

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    const arrivals: Array<{ text: string; at: number }> = [];
    const t0 = Date.now();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      arrivals.push({ text: decoder.decode(value, { stream: true }), at: Date.now() - t0 });
    }

    const full = arrivals.map((a) => a.text).join("");
    expect(full).toContain('data: {"n":1}');
    expect(full).toContain('data: {"n":3}');
    // Streaming proof: the first chunk must arrive well before the upstream
    // closes (~90ms). A buffering implementation would deliver everything in
    // one burst at the end.
    expect(arrivals.length).toBeGreaterThan(1);
    expect(arrivals[0].at).toBeLessThan(70);
  });

  it("returns non-SSE responses intact with status and headers", async () => {
    const res = await proxyViaNodeHttp(`${baseUrl}/json`, { method: "GET", headers: new Headers() });

    expect(res.status).toBe(200);
    expect(res.headers.get("x-upstream")).toBe("yes");
    expect(await res.json()).toEqual({ ok: true });
  });

  it("forwards request bodies (POST echo round-trip)", async () => {
    const payload = JSON.stringify({ threadId: "t-1", messages: [] });
    const res = await proxyViaNodeHttp(`${baseUrl}/echo`, {
      method: "POST",
      headers: new Headers({ "content-type": "application/json" }),
      body: payload,
    });

    expect(res.status).toBe(200);
    expect(await res.text()).toBe(payload);
  });

  it("handles null-body statuses without throwing", async () => {
    // Response constructor throws on 204-with-body — regression guard for
    // the MCP-app iframe-context endpoint (see route.ts NULL_BODY_STATUSES).
    const res = await proxyViaNodeHttp(`${baseUrl}/nobody`, { method: "GET", headers: new Headers() });
    expect(res.status).toBe(204);
    expect(res.body).toBeNull();
  });

  it("propagates upstream error statuses as responses, not exceptions", async () => {
    const res = await proxyViaNodeHttp(`${baseUrl}/missing`, { method: "GET", headers: new Headers() });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "not_found" });
  });

  it("rejects on connection refused so route.ts can map it to a 502", async () => {
    await expect(proxyViaNodeHttp("http://127.0.0.1:9/nothing", { method: "GET", headers: new Headers() })).rejects.toThrow();
  });
});
