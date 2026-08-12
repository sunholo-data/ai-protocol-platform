// Tests for the Aitana MCP sandbox proxy server.
// Covers the security-critical CSP builder + sanitizer + HTTP route surface.

import { describe, expect, it } from "vitest";

import { buildCspHeader, sanitizeCspDomains } from "../serve";

describe("sanitizeCspDomains", () => {
  it("returns empty for undefined", () => {
    expect(sanitizeCspDomains(undefined)).toEqual([]);
  });

  it("returns empty for empty input", () => {
    expect(sanitizeCspDomains([])).toEqual([]);
  });

  it("preserves valid domains", () => {
    expect(sanitizeCspDomains(["https://api.example.com", "https://cdn.example.com"]))
      .toEqual(["https://api.example.com", "https://cdn.example.com"]);
  });

  it("rejects entries with semicolons (CSP directive breakout)", () => {
    expect(sanitizeCspDomains(["https://evil.com; script-src *"]))
      .toEqual([]);
  });

  it("rejects entries with newlines (CSP directive breakout)", () => {
    expect(sanitizeCspDomains(["https://evil.com\nscript-src *"]))
      .toEqual([]);
  });

  it("rejects entries with single quotes (CSP keyword injection)", () => {
    expect(sanitizeCspDomains(["'unsafe-eval'"]))
      .toEqual([]);
  });

  it("rejects entries with double quotes", () => {
    expect(sanitizeCspDomains(['https://evil.com" "*']))
      .toEqual([]);
  });

  it("rejects entries with spaces (multiple sources in one entry)", () => {
    expect(sanitizeCspDomains(["https://a.com https://b.com"]))
      .toEqual([]);
  });

  it("rejects non-string entries", () => {
    // @ts-expect-error — testing runtime type guard
    expect(sanitizeCspDomains([null, 42, "https://ok.com"]))
      .toEqual(["https://ok.com"]);
  });
});

describe("buildCspHeader", () => {
  it("returns a default CSP when csp is undefined", () => {
    const header = buildCspHeader(undefined);
    expect(header).toContain("default-src 'self' 'unsafe-inline'");
    expect(header).toContain("script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data:");
    expect(header).toContain("frame-src 'none'");
    expect(header).toContain("object-src 'none'");
    expect(header).toContain("base-uri 'none'");
  });

  it("includes resource domains in script-src and style-src", () => {
    const header = buildCspHeader({
      resourceDomains: ["https://cdn.example.com"],
    });
    expect(header).toContain("script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data: https://cdn.example.com");
    expect(header).toContain("style-src 'self' 'unsafe-inline' blob: data: https://cdn.example.com");
    expect(header).toContain("worker-src 'self' blob: https://cdn.example.com");
  });

  it("includes connect domains in connect-src", () => {
    const header = buildCspHeader({
      connectDomains: ["https://api.example.com"],
    });
    expect(header).toContain("connect-src 'self' https://api.example.com");
  });

  it("uses frame-src 'none' when frameDomains absent", () => {
    const header = buildCspHeader({});
    expect(header).toContain("frame-src 'none'");
  });

  it("uses provided frameDomains when present", () => {
    const header = buildCspHeader({
      frameDomains: ["https://embed.example.com"],
    });
    expect(header).toContain("frame-src https://embed.example.com");
  });

  it("strips malicious resourceDomain entries silently (sanitization gate)", () => {
    const header = buildCspHeader({
      resourceDomains: ["https://ok.com", "https://evil.com; script-src *"],
    });
    expect(header).toContain("https://ok.com");
    expect(header).not.toContain("script-src *");
    expect(header).not.toContain("evil.com");
  });

  it("uses base-uri 'none' when baseUriDomains absent (defence-in-depth)", () => {
    const header = buildCspHeader({});
    expect(header).toContain("base-uri 'none'");
  });
});

describe("createSandboxApp HTTP routes", () => {
  it("serves /healthz with allowedHostOrigins (smoke target for deploy probes)", async () => {
    const { createSandboxApp } = await import("../serve");
    const app = createSandboxApp();
    const { default: request } = await import("supertest").catch(async () => {
      // supertest isn't a dep; fall back to inline express.test() via http
      throw new Error(
        "supertest not installed — skip this test or add as dev dep. " +
          "For now the smoke probe lives in scripts/smoke-deployed.sh.",
      );
    });
    const res = await request(app).get("/healthz");
    expect(res.status).toBe(200);
    expect(res.body).toMatchObject({ status: "ok" });
    expect(Array.isArray(res.body.allowedHostOrigins)).toBe(true);
  });

  it("/artefacts/* responses set the strict ADR-013 CSP", async () => {
    const { mkdirSync, writeFileSync, rmSync } = await import("node:fs");
    const { dirname, join: pjoin } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const ARTEFACT_DIR = pjoin(__dirname, "..", "artefacts", "__test", "v1");
    mkdirSync(ARTEFACT_DIR, { recursive: true });
    writeFileSync(
      pjoin(ARTEFACT_DIR, "index.html"),
      "<!doctype html><html><body>test</body></html>",
    );

    try {
      const { createSandboxApp } = await import("../serve");
      const app = createSandboxApp();
      const { default: request } = await import("supertest");
      const res = await request(app).get("/artefacts/__test/v1/index.html");
      expect(res.status).toBe(200);
      const csp = res.headers["content-security-policy"];
      expect(csp).toBeDefined();
      expect(csp).toContain("default-src 'none'");
      expect(csp).toContain("connect-src 'none'");
      expect(csp).toContain("frame-src 'none'");
      expect(csp).toContain("object-src 'none'");
      expect(csp).toContain("base-uri 'none'");
      expect(res.headers["referrer-policy"]).toBe("no-referrer");
      expect(res.headers["x-content-type-options"]).toBe("nosniff");
    } finally {
      rmSync(pjoin(__dirname, "..", "artefacts", "__test"), { recursive: true, force: true });
    }
  });

  it("/sandbox.html serves the proxy bridge with an injected runtime config", async () => {
    const { createSandboxApp } = await import("../serve");
    const app = createSandboxApp();
    const { default: request } = await import("supertest");
    const res = await request(app).get("/sandbox.html");
    expect(res.status).toBe(200);
    expect(res.text).toContain('src="/sandbox.js"');
    expect(res.text).toContain("window.__AITANA_SANDBOX_CONFIG__");
    expect(res.text).toContain("allowedReferrerPattern");
    const csp = res.headers["content-security-policy"];
    expect(csp).toBeDefined();
    expect(csp).toContain("script-src");
  });

  it("/sandbox.html accepts a ?csp= query for tailoring per-artefact CSP", async () => {
    const { createSandboxApp } = await import("../serve");
    const app = createSandboxApp();
    const { default: request } = await import("supertest");
    const cspParam = JSON.stringify({
      connectDomains: ["https://api.example.com"],
      resourceDomains: ["https://cdn.example.com"],
    });
    const res = await request(app).get(`/sandbox.html?csp=${encodeURIComponent(cspParam)}`);
    expect(res.status).toBe(200);
    const header = res.headers["content-security-policy"];
    expect(header).toContain("https://api.example.com");
    expect(header).toContain("https://cdn.example.com");
  });

  it("/artefacts/*/assets/*.wasm gets immutable cache + application/wasm + brotli", async () => {
    const { mkdirSync, writeFileSync, rmSync } = await import("node:fs");
    const { dirname, join: pjoin } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const ASSETS = pjoin(__dirname, "..", "artefacts", "__wasmtest", "v1", "assets");
    mkdirSync(ASSETS, { recursive: true });
    // Fake wasm + a (distinguishable) brotli sibling — the route serves bytes
    // as-is; the test only asserts headers + which body is chosen.
    writeFileSync(pjoin(ASSETS, "ailang.wasm"), Buffer.from("RAW-WASM-BYTES"));
    writeFileSync(pjoin(ASSETS, "ailang.wasm.br"), Buffer.from("BR"));

    try {
      const { createSandboxApp } = await import("../serve");
      const app = createSandboxApp();
      const { default: request } = await import("supertest");

      const br = await request(app)
        .get("/artefacts/__wasmtest/v1/assets/ailang.wasm")
        .set("Accept-Encoding", "br");
      expect(br.status).toBe(200);
      expect(br.headers["content-type"]).toContain("application/wasm");
      expect(br.headers["cache-control"]).toBe("public, max-age=31536000, immutable");
      expect(br.headers["content-encoding"]).toBe("br");
      expect(br.headers["vary"]).toContain("Accept-Encoding");

      const raw = await request(app)
        .get("/artefacts/__wasmtest/v1/assets/ailang.wasm")
        .set("Accept-Encoding", "identity");
      expect(raw.status).toBe(200);
      expect(raw.headers["content-type"]).toContain("application/wasm");
      expect(raw.headers["cache-control"]).toBe("public, max-age=31536000, immutable");
      expect(raw.headers["content-encoding"]).toBeUndefined();
    } finally {
      rmSync(pjoin(__dirname, "..", "artefacts", "__wasmtest"), { recursive: true, force: true });
    }
  });

  it("/artefacts/* refuses to serve dotfiles (.env, .gitkeep, etc.)", async () => {
    const { mkdirSync, writeFileSync, rmSync } = await import("node:fs");
    const { dirname, join: pjoin } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const ARTEFACTS_BASE = pjoin(__dirname, "..", "artefacts");
    mkdirSync(ARTEFACTS_BASE, { recursive: true });
    const DOTFILE = pjoin(ARTEFACTS_BASE, ".env");
    writeFileSync(DOTFILE, "SECRET=should-not-be-served");

    try {
      const { createSandboxApp } = await import("../serve");
      const app = createSandboxApp();
      const { default: request } = await import("supertest");
      const res = await request(app).get("/artefacts/.env");
      expect(res.status).not.toBe(200);
      expect(res.text).not.toContain("SECRET=should-not-be-served");
    } finally {
      rmSync(DOTFILE, { force: true });
    }
  });
});
