// Workshop W5b — The Proxy: Why It Exists
// In Cloud Run, frontend and backend are separate services. The browser reaches
// one origin, so every /api/proxy/** request forwards to the backend sidecar.
// Three gotchas baked in here that are worth calling out in the talk:
//   1. IPv4 literal (127.0.0.1) not "localhost" — Node DNS can resolve localhost
//      to ::1 while uvicorn only listens on IPv4. Silent failure, hard to diagnose.
//   2. SSE passthrough (~line 120): detect text/event-stream and stream the body
//      directly. Buffer it first and you turn SSE into a single delayed response.
//   3. NO undici in the transport (MODEL-RELIABILITY M1): Next's fetch() is
//      undici, whose bodyTimeout killed a healthy 5-minute stream in the v5
//      long-stream incident (UND_ERR_BODY_TIMEOUT). All upstream requests go via
//      node:http (see ./nodeProxy.ts), which has no body timeout by design.

import { NextResponse, type NextRequest } from "next/server";

import { proxyViaNodeHttp } from "../nodeProxy";

export const dynamic = "force-dynamic";

/**
 * Catch-all proxy to the sidecar backend.
 *
 * Forwards every method with its body + query string to `${BACKEND_URL}/<path>`,
 * passing the client's `Authorization: Bearer <jwt>` header through untouched
 * so the backend's `Depends(get_current_user)` can verify it. Strips
 * hop-by-hop + Next-internal headers the upstream shouldn't see.
 *
 * Gotchas baked in from FE-BRINGUP-1 postmortem (docs/ops/incidents/
 * fe-bringup-1-proxy-404.md):
 *   - Default backend is `http://127.0.0.1:1956` (literal IPv4, not
 *     "localhost" — Node's DNS can resolve localhost to ::1 while uvicorn
 *     only listens on IPv4, which fails silently).
 *   - Never fall back to `:8080` — that's Next's own ingress port in Cloud
 *     Run, which would loop requests back into this process and return a
 *     Next 404 HTML page.
 *   - Regression guard: a curl to `/api/proxy/api/skills` without a Bearer
 *     token must return a JSON 401 from the backend, never a Next 404.
 */

// G20: `||` not `??` — Cloud Run sets every declared env var, absent vars
// arrive as "" which `??` doesn't catch (it only falls back on null/undefined).
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:1956";

// Headers that must not be forwarded upstream: hop-by-hop, Next-internal, or
// ones that would confuse the upstream server (host rewriting).
const BLOCKED_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "upgrade",
  // Next/fetch-internal
  "x-middleware-invoke",
  "x-invoke-path",
  "x-invoke-query",
]);

// HTTP statuses that must not carry a body (per the Fetch spec). The Web
// Response constructor throws if you pass a non-null body with one of these.
const NULL_BODY_STATUSES = new Set([101, 103, 204, 205, 304]);

// Response headers we should not echo back (fetch sets Content-Length itself;
// hop-by-hop headers don't belong in a Next response).
const BLOCKED_RESPONSE_HEADERS = new Set([
  "connection",
  "content-length",
  "transfer-encoding",
  "keep-alive",
]);

function buildUpstreamUrl(req: NextRequest, path: string[]): string {
  const joined = path.map(encodeURIComponent).join("/");
  const qs = req.nextUrl.search; // includes leading "?" or empty
  return `${BACKEND_URL}/${joined}${qs}`;
}

function filterRequestHeaders(headers: Headers): Headers {
  const out = new Headers();
  headers.forEach((value, key) => {
    if (!BLOCKED_REQUEST_HEADERS.has(key.toLowerCase())) {
      out.set(key, value);
    }
  });
  return out;
}

function filterResponseHeaders(headers: Headers): Headers {
  const out = new Headers();
  headers.forEach((value, key) => {
    if (!BLOCKED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      out.set(key, value);
    }
  });
  return out;
}

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<NextResponse> {
  const { path } = await ctx.params;
  const url = buildUpstreamUrl(req, path);

  const init = {
    method: req.method,
    headers: filterRequestHeaders(req.headers),
    // Only attach a body for methods that can have one.
    body: req.method !== "GET" && req.method !== "HEAD" ? req.body : undefined,
  };

  try {
    const upstream = await proxyViaNodeHttp(url, init);
    const contentType = upstream.headers.get("content-type") ?? "";
    if (contentType.includes("text/event-stream")) {
      return new NextResponse(upstream.body, {
        status: upstream.status,
        headers: filterResponseHeaders(upstream.headers),
      });
    }
    // Null-body statuses (101/103/204/205/304) MUST be constructed with a
    // null body — passing a buffer throws `TypeError: Response constructor:
    // Invalid response status code 204`, which the catch below turns into a
    // spurious 502. Sprint 1.25's iframe-context endpoint returns 204, so
    // skipping this branch breaks the active-bridge half of MCP-app integration.
    if (NULL_BODY_STATUSES.has(upstream.status)) {
      return new NextResponse(null, {
        status: upstream.status,
        headers: filterResponseHeaders(upstream.headers),
      });
    }
    const body = await upstream.arrayBuffer();
    return new NextResponse(body, {
      status: upstream.status,
      headers: filterResponseHeaders(upstream.headers),
    });
  } catch (err) {
    return NextResponse.json(
      { error: "backend_unreachable", message: String(err) },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
