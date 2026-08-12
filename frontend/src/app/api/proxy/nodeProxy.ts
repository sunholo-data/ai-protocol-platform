// MODEL-RELIABILITY M1 — proxy transport without undici.
//
// Next's global fetch() is undici, whose bodyTimeout killed a healthy
// 5-minute Anthropic stream in the v5 "long-stream incident" (UND_ERR_BODY_TIMEOUT
// at exactly 300s, despite keep-alive pings — the timeout's idle-vs-total
// semantics have shifted across Node versions, so we don't reason about
// them; we remove undici from the path). node:http has no body timeout by
// design. ALL upstream proxy requests go through here — one client, no
// pre-detection of streaming-ness; route.ts branches on the *response*
// content-type exactly as it did with fetch().

import http from "node:http";
import https from "node:https";
import { Readable } from "node:stream";

// Statuses the Web Response constructor requires a null body for.
const NULL_BODY_STATUSES = new Set([101, 103, 204, 205, 304]);

export interface NodeProxyInit {
  method: string;
  headers: Headers;
  body?: string | ReadableStream<Uint8Array> | null;
  signal?: AbortSignal;
}

function headersToObject(headers: Headers): Record<string, string> {
  const out: Record<string, string> = {};
  headers.forEach((value, key) => {
    out[key] = value;
  });
  return out;
}

function incomingHeaders(raw: http.IncomingHttpHeaders): Headers {
  const out = new Headers();
  for (const [key, value] of Object.entries(raw)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const v of value) out.append(key, v);
    } else {
      out.set(key, value);
    }
  }
  return out;
}

/**
 * Issue `init` against `url` via node:http(s) and return a Web `Response`
 * whose body streams chunk-by-chunk as the upstream produces it.
 *
 * - Rejects on connection-level errors (route.ts maps those to a 502).
 * - Upstream HTTP error statuses resolve normally — they're responses.
 * - No socket idle timeout is set: that is the entire point of this module.
 */
export function proxyViaNodeHttp(url: string, init: NodeProxyInit): Promise<Response> {
  const target = new URL(url);
  const client = target.protocol === "https:" ? https : http;

  return new Promise<Response>((resolve, reject) => {
    const req = client.request(
      {
        protocol: target.protocol,
        hostname: target.hostname,
        port: target.port,
        path: `${target.pathname}${target.search}`,
        method: init.method,
        headers: headersToObject(init.headers),
      },
      (res) => {
        const status = res.statusCode ?? 502;
        const headers = incomingHeaders(res.headers);
        if (NULL_BODY_STATUSES.has(status)) {
          // Drain so the socket is reusable, then hand back a bodyless
          // Response (the constructor throws on 204-with-body).
          res.resume();
          resolve(new Response(null, { status, headers }));
          return;
        }
        const body = Readable.toWeb(res) as ReadableStream<Uint8Array>;
        resolve(new Response(body, { status, headers }));
      },
    );

    req.on("error", reject);

    if (init.signal) {
      const onAbort = () => req.destroy(new Error("proxy request aborted"));
      if (init.signal.aborted) onAbort();
      else init.signal.addEventListener("abort", onAbort, { once: true });
    }

    if (init.body === undefined || init.body === null) {
      req.end();
    } else if (typeof init.body === "string") {
      req.end(init.body);
    } else {
      // Web ReadableStream (e.g. a forwarded upload) → node stream → request.
      Readable.fromWeb(init.body as Parameters<typeof Readable.fromWeb>[0])
        .on("error", (err) => req.destroy(err))
        .pipe(req);
    }
  });
}
