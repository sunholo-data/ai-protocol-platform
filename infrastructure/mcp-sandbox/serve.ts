// Aitana MCP App sandbox proxy server.
//
// Serves sandbox.html + sandbox.js on a different origin than the Aitana
// host (frontend), per MCP Apps spec — allow-same-origin on the inner
// iframe is only safe when the sandbox is on its own origin.
//
// Local dev: SANDBOX_PORT defaults to 3457 (next to frontend's 3456).
// Cloud Run: gets a unique *.run.app URL distinct from the frontend's.
//
// CSP is set via HTTP headers built from the ?csp=<json> query param so
// that the served HTML can't tamper with its own CSP (which it could via
// meta tags). Domain entries are sanitized to reject anything that could
// break out of a directive.
//
// Adapted from `modelcontextprotocol/ext-apps/examples/basic-host/serve.ts`
// (commit 0008d3b7, ext-apps 1.7.1). Differences from the reference:
// - Host server (port 8080) removed — Aitana's Next.js frontend IS the host
// - SANDBOX_PORT defaults to 3457 (matches scripts/dev.sh orchestration)
// - Allowed-host-origins regex is injected into sandbox.html at request
//   time via window.__AITANA_SANDBOX_CONFIG__ so the regex isn't baked
//   into the bundle
// - Logging prefix changed from [Sandbox] to [aitana-sandbox]

import cors from "cors";
import express from "express";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

// Inlined from @modelcontextprotocol/ext-apps spec.types — keeps the
// service free of any runtime dep on the SDK (only the type surface used).
interface McpUiResourceCsp {
  resourceDomains?: string[];
  connectDomains?: string[];
  frameDomains?: string[];
  baseUriDomains?: string[];
}

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Cloud Run convention: respect PORT env var (set by the runtime to 8080).
// Fall back to SANDBOX_PORT for the local dev workflow (scripts/dev.sh
// orchestrates frontend:3456, backend:1956, sandbox:3457). Final fallback
// to 3457 keeps the local default behaviour unchanged.
const SANDBOX_PORT = parseInt(
  process.env.PORT || process.env.SANDBOX_PORT || "3457",
  10,
);
const PUBLIC_DIR = join(__dirname, "public");

// Comma-separated list of host origins allowed to embed this sandbox.
// Default targets local dev. Set to e.g. "https://platform-frontend-dev-xxx.run.app"
// in deployed envs.
const ALLOWED_HOST_ORIGINS_RAW = process.env.ALLOWED_HOST_ORIGINS ?? "http://localhost:3456";
const ALLOWED_HOST_ORIGINS = ALLOWED_HOST_ORIGINS_RAW
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

// Build a regex string suitable for the `RegExp(...)` ctor in sandbox.ts.
// Each origin becomes an exact match anchored at start, with optional path/port.
function buildAllowedReferrerPattern(origins: string[]): string {
  if (origins.length === 0) {
    throw new Error("ALLOWED_HOST_ORIGINS must contain at least one origin");
  }
  const escaped = origins.map((o) => o.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `^(${escaped.join("|")})(:|/|$)`;
}

const ALLOWED_REFERRER_PATTERN = buildAllowedReferrerPattern(ALLOWED_HOST_ORIGINS);

// Pre-read sandbox.html so we can inject the runtime config script.
const SANDBOX_HTML_RAW = readFileSync(join(PUBLIC_DIR, "sandbox.html"), "utf8");
const SANDBOX_HTML = SANDBOX_HTML_RAW.replace(
  '<script type="module" src="/sandbox.js"></script>',
  `<script>window.__AITANA_SANDBOX_CONFIG__ = ${JSON.stringify({
    allowedReferrerPattern: ALLOWED_REFERRER_PATTERN,
  })};</script>\n    <script type="module" src="/sandbox.js"></script>`,
);

// Validate CSP domain entries to prevent injection attacks.
// Rejects entries containing characters that could:
// - `;` or newlines: break out to new CSP directive
// - quotes: inject CSP keywords like 'unsafe-eval'
// - space: inject multiple sources in one entry
export function sanitizeCspDomains(domains?: string[]): string[] {
  if (!domains) return [];
  return domains.filter((d) => typeof d === "string" && !/[;\r\n'" ]/.test(d));
}

export function buildCspHeader(csp?: McpUiResourceCsp): string {
  const resourceDomains = sanitizeCspDomains(csp?.resourceDomains).join(" ");
  const connectDomains = sanitizeCspDomains(csp?.connectDomains).join(" ");
  const frameDomains = sanitizeCspDomains(csp?.frameDomains).join(" ") || null;
  const baseUriDomains =
    sanitizeCspDomains(csp?.baseUriDomains).join(" ") || null;

  const directives = [
    "default-src 'self' 'unsafe-inline'",
    `script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data: ${resourceDomains}`.trim(),
    `style-src 'self' 'unsafe-inline' blob: data: ${resourceDomains}`.trim(),
    `img-src 'self' data: blob: ${resourceDomains}`.trim(),
    `font-src 'self' data: blob: ${resourceDomains}`.trim(),
    `media-src 'self' data: blob: ${resourceDomains}`.trim(),
    `connect-src 'self' ${connectDomains}`.trim(),
    `worker-src 'self' blob: ${resourceDomains}`.trim(),
    frameDomains ? `frame-src ${frameDomains}` : "frame-src 'none'",
    "object-src 'none'",
    baseUriDomains ? `base-uri ${baseUriDomains}` : "base-uri 'none'",
  ];

  return directives.join("; ");
}

export function createSandboxApp(): express.Express {
  const app = express();
  app.use(cors());

  // Serve sandbox.html (with injected runtime config) at / and /sandbox.html
  app.get(["/", "/sandbox.html"], (req, res) => {
    let cspConfig: McpUiResourceCsp | undefined;
    if (typeof req.query.csp === "string") {
      try {
        cspConfig = JSON.parse(req.query.csp);
      } catch (e) {
        console.warn("[aitana-sandbox] Invalid CSP query param:", e);
      }
    }
    res.setHeader("Content-Security-Policy", buildCspHeader(cspConfig));
    res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
    res.setHeader("Pragma", "no-cache");
    res.setHeader("Expires", "0");
    res.type("html").send(SANDBOX_HTML);
  });

  // Serve the bundled bridge script.
  app.get("/sandbox.js", (_req, res) => {
    res.setHeader("Cache-Control", "public, max-age=300");
    res.sendFile(join(PUBLIC_DIR, "sandbox.js"));
  });

  // Health probe for Cloud Run / smoke tests.
  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok", allowedHostOrigins: ALLOWED_HOST_ORIGINS });
  });

  // Static artefact subtree — hand-curated MCP App content served from
  // the sandbox origin so `StaticArtefactFrame` can fetch and inject
  // them via the sandbox-proxy architecture (spec §Sandbox proxy).
  //
  // Each artefact lives at:
  //   `infrastructure/mcp-sandbox/artefacts/<name>/v<version>/index.html`
  //
  // The host frontend fetches the HTML on `ui/notifications/sandbox-proxy-ready`
  // and pushes it to the proxy via `ui/notifications/sandbox-resource-ready`.
  // The proxy document.writes it into its inner iframe.
  //
  // Security: strict CSP (no external resources, no nested iframes);
  // dotfiles denied; directory listing disabled (`index: false`).
  // The artefact HTML is a single self-contained file reviewed at commit time.
  const ARTEFACTS_DIR = join(__dirname, "artefacts");
  const ARTEFACT_CSP = [
    "default-src 'none'",
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    "img-src data: blob:",
    "font-src data:",
    "connect-src 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
  // WASM runtime assets (e.g. the ppa-obligation-analysis deontic engine) are
  // large (~40 MB raw) and content-addressed by a pinned release version, so
  // they get: (1) immutable, one-year cache — the version lives in the fetch
  // pipeline, a given URL never changes bytes; (2) brotli — a precompressed
  // `<name>.wasm.br` sibling (built by scripts/fetch-ailang-wasm.sh) is served
  // with `Content-Encoding: br` when the client accepts it (~5.7 MB vs 40 MB),
  // else the raw wasm streams. Registered BEFORE the generic /artefacts static
  // handler so it wins for `.wasm`. Falls through (next()) to the static
  // handler if the file is absent.
  app.get(/^\/artefacts\/.+\/assets\/[^/]+\.wasm$/, (req, res, next) => {
    const rel = normalize(decodeURIComponent(req.path)).replace(/^(\.\.[/\\])+/, "");
    const filePath = join(__dirname, rel);
    // Path-traversal guard: the resolved path must stay under ARTEFACTS_DIR.
    if (!filePath.startsWith(ARTEFACTS_DIR) || !existsSync(filePath)) return next();

    res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
    res.setHeader("Content-Type", "application/wasm");
    res.setHeader("Content-Security-Policy", ARTEFACT_CSP);
    res.setHeader("Referrer-Policy", "no-referrer");
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Vary", "Accept-Encoding");

    const acceptsBr = /(^|,)\s*br\s*(;|,|$)/.test(String(req.headers["accept-encoding"] || ""));
    const brPath = `${filePath}.br`;
    if (acceptsBr && existsSync(brPath)) {
      res.setHeader("Content-Encoding", "br");
      res.setHeader("Content-Length", String(statSync(brPath).size));
      createReadStream(brPath).pipe(res);
      return;
    }
    res.setHeader("Content-Length", String(statSync(filePath).size));
    createReadStream(filePath).pipe(res);
  });

  app.use(
    "/artefacts",
    express.static(ARTEFACTS_DIR, {
      index: false,
      dotfiles: "deny",
      maxAge: "5m",
      setHeaders: (res) => {
        res.setHeader("Cache-Control", "public, max-age=300");
        res.setHeader("Content-Security-Policy", ARTEFACT_CSP);
        res.setHeader("Referrer-Policy", "no-referrer");
        res.setHeader("X-Content-Type-Options", "nosniff");
      },
    }),
  );

  // Anything else 404s — this server intentionally serves a tiny surface.
  app.use((_req, res) => {
    res.status(404).send("Only sandbox.html / sandbox.js / healthz / artefacts/* are served.");
  });

  return app;
}

// Start the server when run directly (not when imported by tests).
const isMainModule = import.meta.url === `file://${process.argv[1]}`;
if (isMainModule) {
  const app = createSandboxApp();
  app.listen(SANDBOX_PORT, (err?: Error) => {
    if (err) {
      console.error("[aitana-sandbox] Error starting server:", err);
      process.exit(1);
    }
    console.log(`[aitana-sandbox] Listening on http://localhost:${SANDBOX_PORT}`);
    console.log(`[aitana-sandbox] Allowed host origins: ${ALLOWED_HOST_ORIGINS.join(", ")}`);
  });
}
