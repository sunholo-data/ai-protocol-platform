// G43 (template-a2a-spec-compliance.md): tests for the well-known A2A
// agent-card proxy route. Two coupled jobs the route does:
//
//   1. G39 — proxy the request to the backend sidecar and preserve the
//      X-A2A-Extensions / Vary negotiation header round-trip.
//   2. G43 — rewrite the card body's `url` field to the public origin
//      so peer A2A clients (and Gemini Enterprise) can actually invoke
//      the agent. The backend can't know its own public URL because
//      it's a sidecar behind this ingress.
//
// These tests pin both contracts so future refactors don't accidentally
// remove the rewrite (silent regression that only surfaces at Gemini
// Enterprise registration time — exactly the bug G43 was opened on).

import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "../route";

function makeRequest(
  url: string,
  headers: Record<string, string> = {},
): NextRequest {
  return new NextRequest(url, { headers });
}

const FAKE_CARD = {
  protocolVersion: "0.2.0",
  name: "Test Agent",
  description: "Test description",
  // G45 / Sprint A2A-INVOKE: backend now advertises the /a2a invocation
  // path on its url (was bare `http://localhost:1956` pre-G45). The
  // frontend proxy must swap origin AND preserve the path so peers POST
  // `message/send` at the right place.
  url: "http://localhost:1956/a2a",
  version: "6.0.0",
  capabilities: {
    streaming: true,
    pushNotifications: false,
    stateTransitionHistory: false,
    extensions: [{ uri: "https://a2aproject.github.io/A2A/v0.2", required: false }],
  },
  defaultInputModes: ["text"],
  defaultOutputModes: ["text"],
  skills: [],
};

function mockFetchJson(card: Record<string, unknown>, responseHeaders: Record<string, string> = {}) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(card), {
      status: 200,
      headers: {
        "content-type": "application/json",
        ...responseHeaders,
      },
    }),
  );
}

describe("/.well-known/agent.json route — G43 url-rewrite", () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("rewrites card.url to X-Forwarded-Proto + X-Forwarded-Host when both present (Cloud Run path)", async () => {
    global.fetch = mockFetchJson(FAKE_CARD) as typeof global.fetch;
    const req = makeRequest("http://internal/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "my-fork.run.app",
    });

    const resp = await GET(req);
    const body = await resp.json();

    expect(body.url).toBe("https://my-fork.run.app/a2a");
    // Other card fields untouched.
    expect(body.protocolVersion).toBe("0.2.0");
    expect(body.skills).toEqual([]);
  });

  it("handles a comma-separated X-Forwarded-Proto chain by taking the leftmost token (RFC 7239)", async () => {
    global.fetch = mockFetchJson(FAKE_CARD) as typeof global.fetch;
    const req = makeRequest("http://internal/.well-known/agent.json", {
      "x-forwarded-proto": "https,http",
      "x-forwarded-host": "client.example.com,internal.run.app",
    });

    const resp = await GET(req);
    const body = await resp.json();

    // Leftmost in each header = original client-facing values.
    expect(body.url).toBe("https://client.example.com/a2a");
  });

  it("falls back to Host header + nextUrl.protocol when X-Forwarded-* absent (local dev)", async () => {
    global.fetch = mockFetchJson(FAKE_CARD) as typeof global.fetch;
    // Local dev: nextUrl has scheme http, host carries the bound port.
    const req = makeRequest("http://localhost:3000/.well-known/agent.json", {
      host: "localhost:3000",
    });

    const resp = await GET(req);
    const body = await resp.json();

    expect(body.url).toBe("http://localhost:3000/a2a");
  });

  it("forwards X-A2A-Extensions request header upstream so backend can negotiate", async () => {
    const fetchSpy = mockFetchJson(FAKE_CARD);
    global.fetch = fetchSpy as typeof global.fetch;
    const req = makeRequest("http://x/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "fork.run.app",
      "x-a2a-extensions": "a2ui-v0.9, a2ui-decoupled-pattern",
    });

    await GET(req);

    const [, init] = fetchSpy.mock.calls[0];
    expect(init.headers["x-a2a-extensions"]).toBe("a2ui-v0.9, a2ui-decoupled-pattern");
  });

  it("preserves X-A2A-Extensions + Vary response headers from the backend", async () => {
    global.fetch = mockFetchJson(FAKE_CARD, {
      "x-a2a-extensions": "a2ui-v0.9",
      vary: "X-A2A-Extensions",
    }) as typeof global.fetch;
    const req = makeRequest("http://x/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "fork.run.app",
    });

    const resp = await GET(req);

    expect(resp.headers.get("x-a2a-extensions")).toBe("a2ui-v0.9");
    expect(resp.headers.get("vary")).toBe("X-A2A-Extensions");
  });

  it("passes non-JSON upstream responses through untouched (don't silently rewrite error bodies)", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response("<html>backend on fire</html>", {
        status: 500,
        headers: { "content-type": "text/html" },
      }),
    ) as typeof global.fetch;
    const req = makeRequest("http://x/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "fork.run.app",
    });

    const resp = await GET(req);
    const text = await resp.text();

    expect(resp.status).toBe(500);
    expect(resp.headers.get("content-type")).toBe("text/html");
    expect(text).toBe("<html>backend on fire</html>");
    // CRITICAL: did NOT get rewritten through JSON.parse → JSON.stringify.
    expect(() => JSON.parse(text)).toThrow();
  });

  it("passes non-2xx JSON responses through untouched (don't add a url to error JSON)", async () => {
    const errorBody = { error: "skill_marketplace_query_failed" };
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(errorBody), {
        status: 503,
        headers: { "content-type": "application/json" },
      }),
    ) as typeof global.fetch;
    const req = makeRequest("http://x/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "fork.run.app",
    });

    const resp = await GET(req);
    const body = await resp.json();

    expect(resp.status).toBe(503);
    expect(body).toEqual(errorBody);
    // Did NOT get a synthetic .url added.
    expect(body.url).toBeUndefined();
  });

  it("preserves the upstream url path when rewriting origin (G45 path-preservation)", async () => {
    // G45 / Sprint A2A-INVOKE: the backend's card.url advertises the
    // A2A invocation path (`/a2a`). The proxy used to overwrite the
    // whole url with publicOrigin() which silently stripped the path —
    // peers ended up POSTing message/send to the bare host and got 404.
    // The fix swaps origin only, keeping the path (and any query).
    const cardWithPath = { ...FAKE_CARD, url: "http://localhost:1956/a2a?v=1" };
    global.fetch = mockFetchJson(cardWithPath) as typeof global.fetch;
    const req = makeRequest("http://internal/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "example.com",
    });

    const resp = await GET(req);
    const body = await resp.json();

    // Origin swapped, path + query preserved.
    expect(body.url).toBe("https://example.com/a2a?v=1");
  });

  it("falls back to bare public origin when upstream url is malformed (defensive)", async () => {
    const cardWithBadUrl = { ...FAKE_CARD, url: "not a url" };
    global.fetch = mockFetchJson(cardWithBadUrl) as typeof global.fetch;
    const req = makeRequest("http://internal/.well-known/agent.json", {
      "x-forwarded-proto": "https",
      "x-forwarded-host": "example.com",
    });

    const resp = await GET(req);
    const body = await resp.json();

    // Malformed upstream url can't be parsed; we serve a usable card by
    // falling back to the bare public origin rather than 5xx-ing.
    expect(body.url).toBe("https://example.com");
  });

  it("returns 502 with a structured error when the backend is unreachable", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED")) as typeof global.fetch;
    const req = makeRequest("http://x/.well-known/agent.json");

    const resp = await GET(req);
    const body = await resp.json();

    expect(resp.status).toBe(502);
    expect(body.error).toBe("backend_unreachable");
  });
});
