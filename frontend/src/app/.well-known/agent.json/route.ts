import { NextResponse, type NextRequest } from "next/server";

export const dynamic = "force-dynamic";

/**
 * RFC 8615 well-known proxy + A2A `url` rewrite: `/.well-known/agent.json` → backend sidecar.
 *
 * Two coupled jobs, both required for a card that A2A peers (and Gemini
 * Enterprise) can actually consume:
 *
 * 1. **G39** — proxy the request to the backend sidecar at all. Cloud Run
 *    multi-container deploys serve public ingress through the frontend
 *    (Next.js); the backend (FastAPI) listens only on 127.0.0.1:1956. The
 *    A2A spec mandates the RFC 8615 unprefixed `/.well-known/...` path,
 *    so without this route every crawler gets Next's 404 page.
 *
 * 2. **G43** — REWRITE the card body's `url` field to the public origin
 *    before serving. The backend can't know its own public URL because
 *    it's a sidecar (no env-var-injected `PUBLIC_BASE_URL` in deploy
 *    today); left alone, the card advertises `http://localhost:1956`,
 *    which means A2A peers can DISCOVER the agent but cannot INVOKE any
 *    skill. The Next ingress is the only layer that knows the real
 *    public URL — Cloud Run terminates TLS at the GFE and forwards via
 *    `X-Forwarded-Proto` / `X-Forwarded-Host`, so those headers are the
 *    authoritative source for what URL the outside world reaches us by.
 *
 * Per A2A v0.2 spec (see template-a2a-spec-compliance.md G43), three
 * header concerns preserved across the proxy:
 *
 *   - `X-A2A-Extensions` request header → forwarded upstream so the
 *     backend can negotiate which extensions to advertise.
 *   - `X-A2A-Extensions` + `Vary` response headers → echoed back unchanged
 *     so the crawler sees the negotiated set, not the union.
 *   - `Content-Type: application/json` → preserved (or defaulted) so the
 *     rewritten body is still parsed correctly downstream.
 *
 * Smoke:
 *   curl https://<deployed-url>/.well-known/agent.json \
 *     -H 'X-A2A-Extensions: a2ui-v0.9, a2ui-decoupled-pattern' -i
 *   # → expect: jq .url shows the public URL, not localhost
 *   ./scripts/verify-a2a.sh  # 12-check compliance probe
 */

const FORWARDED_REQUEST_HEADERS = ["x-a2a-extensions", "accept", "accept-language"];
const PRESERVED_RESPONSE_HEADERS = ["x-a2a-extensions", "vary", "cache-control"];

/**
 * Resolve the public origin the agent card should advertise.
 *
 * Priority order:
 *   1. `X-Forwarded-Proto` + `X-Forwarded-Host` headers — Cloud Run's GFE
 *      always sets these; they're the strict edge of the topology and
 *      survive any internal hop-by-hop rewriting.
 *   2. `Host` header + `req.nextUrl.protocol` — local dev / non-Cloud-Run
 *      proxies. Host always present; nextUrl.protocol carries the scheme
 *      Next decoded from the incoming request line.
 *   3. `req.nextUrl.origin` — last-resort fallback when neither header set
 *      is present (e.g. direct internal call without going through any
 *      ingress).
 */
function publicOrigin(req: NextRequest): string {
  const xfProto = req.headers.get("x-forwarded-proto");
  const xfHost = req.headers.get("x-forwarded-host");
  if (xfProto && xfHost) {
    // X-Forwarded-Proto can be a comma-separated chain ("https,http") —
    // the leftmost token is the original client-facing scheme per RFC 7239.
    const proto = xfProto.split(",")[0].trim();
    const host = xfHost.split(",")[0].trim();
    return `${proto}://${host}`;
  }
  const host = req.headers.get("host");
  if (host) {
    const proto = req.nextUrl.protocol.replace(":", "");
    return `${proto}://${host}`;
  }
  return req.nextUrl.origin;
}

export async function GET(req: NextRequest) {
  // G20: `||` not `??` for env defaults (Cloud Run delivers absent vars as "").
  const backend = process.env.BACKEND_URL || "http://127.0.0.1:1956";

  const headers: Record<string, string> = {};
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = req.headers.get(name);
    if (value !== null) headers[name] = value;
  }

  try {
    const upstream = await fetch(`${backend}/.well-known/agent.json`, {
      method: "GET",
      headers,
      cache: "no-store",
    });

    // Preserve negotiation headers regardless of outcome.
    const responseHeaders: Record<string, string> = {};
    for (const name of PRESERVED_RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders[name] = value;
    }
    const upstreamContentType = upstream.headers.get("content-type") ?? "";

    // G43: only rewrite when the upstream responded with JSON (and 2xx).
    // Non-JSON or non-2xx responses pass through untouched so error bodies
    // aren't silently rewritten into something they aren't.
    if (!upstream.ok || !upstreamContentType.includes("application/json")) {
      const passthrough = await upstream.text();
      responseHeaders["content-type"] = upstreamContentType || "application/json";
      return new NextResponse(passthrough, {
        status: upstream.status,
        headers: responseHeaders,
      });
    }

    // Rewrite the card body's `url` field to the public origin BUT preserve
    // the path. G43 (initial fix) rewrote the whole url to publicOrigin(),
    // which silently stripped `/a2a` once the backend started advertising
    // `card.url = "http://localhost:1956/a2a"` (G45 / Sprint A2A-INVOKE).
    // Peer agents need the FULL invocation URL — origin AND path.
    // If the upstream url isn't a parseable URL string (e.g. the backend
    // returned a malformed value), fall back to the bare public origin so
    // the card is still serveable.
    const card = (await upstream.json()) as Record<string, unknown>;
    const newOrigin = publicOrigin(req);
    const upstreamUrlRaw = card.url;
    if (typeof upstreamUrlRaw === "string") {
      try {
        const upstreamUrl = new URL(upstreamUrlRaw);
        card.url = `${newOrigin}${upstreamUrl.pathname}${upstreamUrl.search}`;
      } catch {
        card.url = newOrigin;
      }
    } else {
      card.url = newOrigin;
    }
    responseHeaders["content-type"] = "application/json";
    return new NextResponse(JSON.stringify(card), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (err) {
    return NextResponse.json(
      { error: "backend_unreachable", message: String(err) },
      { status: 502 },
    );
  }
}
