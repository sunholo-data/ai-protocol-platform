/**
 * Every NEXT_PUBLIC_* the app reads must be declared in frontend/Dockerfile.
 *
 * Regression (2026-08-05): the read-aloud (TTS) button never appeared on any
 * deployed env. `NEXT_PUBLIC_ENABLE_READ_ALOUD` was set in frontend/.env.local
 * only, so it worked locally and was **compiled out** of every deployed bundle.
 * Nothing failed: the backend served a valid voice config and
 * /api/voice/config reported `enabled: true` — the button simply didn't exist.
 *
 * NEXT_PUBLIC_* is inlined at COMPILE time, and Docker silently ignores a
 * --build-arg for an ARG it doesn't declare, so this whole class fails as
 * "feature quietly absent in prod", never as a build error. A static check is
 * the only thing that catches it before a user does.
 */

import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const SRC = join(__dirname, "..");
const DOCKERFILE = join(__dirname, "..", "..", "Dockerfile");

/**
 * Vars that are deliberately NOT passed to the deployed build, with the reason
 * each is safe when absent. The allowlist is the point: a NEW flag fails this
 * test until someone consciously decides which side it belongs on, rather than
 * silently shipping disabled (which is what happened to read-aloud).
 */
const INTENTIONALLY_UNDECLARED: Record<string, string> = {
  NEXT_PUBLIC_SHOW_DEV_PROBES: "dev-only affordance; must stay off in deployed envs",
  NEXT_PUBLIC_DEV_LATENCY_HUD: "dev-only latency HUD; must stay off in deployed envs",
  NEXT_PUBLIC_LOCAL_MODE: "local-mode detection; deployed envs are never local mode",
  NEXT_PUBLIC_OBLIGATION_ENGINE_URL:
    "optional engine-placement override (7.6 M3). Unset is the INTENDED default " +
    "— in-browser WASM, no egress. Declare it only when a serve-api sidecar exists.",
};

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === ".next") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry) && !/\.(test|spec)\./.test(entry)) out.push(full);
  }
  return out;
}

describe("NEXT_PUBLIC_* build-time env vars", () => {
  it("are all declared as ARG in the Dockerfile", () => {
    const dockerfile = readFileSync(DOCKERFILE, "utf8");
    const declared = new Set(
      [...dockerfile.matchAll(/^ARG\s+(NEXT_PUBLIC_[A-Z0-9_]+)/gm)].map((m) => m[1]),
    );

    const used = new Map<string, string>();
    for (const file of walk(SRC)) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/process\.env\.(NEXT_PUBLIC_[A-Z0-9_]+)/g)) {
        if (!used.has(m[1])) used.set(m[1], file.slice(SRC.length + 1));
      }
    }

    expect(used.size).toBeGreaterThan(0); // the scanner itself still works

    const missing = [...used.entries()].filter(
      ([name]) => !declared.has(name) && !(name in INTENTIONALLY_UNDECLARED),
    );
    expect(
      missing.map(([name, file]) => `${name} (read in ${file})`),
      "undeclared NEXT_PUBLIC_* vars compile to `undefined` in the deployed " +
        "bundle — the feature is silently absent, with no build error. Add an " +
        "ARG + ENV pair to frontend/Dockerfile and a --build-arg in cloudbuild.yaml.",
    ).toEqual([]);
  });

  it("read-aloud specifically is wired end to end", () => {
    // The one that shipped broken — assert each link in the chain.
    const dockerfile = readFileSync(DOCKERFILE, "utf8");
    expect(dockerfile).toMatch(/^ARG\s+NEXT_PUBLIC_ENABLE_READ_ALOUD$/m);
    expect(dockerfile).toMatch(
      /^ENV\s+NEXT_PUBLIC_ENABLE_READ_ALOUD=\$NEXT_PUBLIC_ENABLE_READ_ALOUD$/m,
    );

    const cloudbuild = readFileSync(
      join(__dirname, "..", "..", "..", "cloudbuild.yaml"),
      "utf8",
    );
    expect(cloudbuild).toContain("_ENABLE_READ_ALOUD:");
    expect(cloudbuild).toContain(
      "--build-arg NEXT_PUBLIC_ENABLE_READ_ALOUD=${_ENABLE_READ_ALOUD}",
    );
  });
});

describe("the allowlist itself", () => {
  it("does not excuse a var that IS declared (stale entry)", () => {
    const declared = new Set(
      [...readFileSync(DOCKERFILE, "utf8").matchAll(/^ARG\s+(NEXT_PUBLIC_[A-Z0-9_]+)/gm)].map(
        (m) => m[1],
      ),
    );
    const stale = Object.keys(INTENTIONALLY_UNDECLARED).filter((n) => declared.has(n));
    expect(stale, "these are declared now — drop them from the allowlist").toEqual([]);
  });

  it("does not excuse a var nothing reads any more", () => {
    const text = walk(SRC)
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    const unused = Object.keys(INTENTIONALLY_UNDECLARED).filter(
      (n) => !text.includes(`process.env.${n}`),
    );
    expect(unused, "no code reads these — drop them from the allowlist").toEqual([]);
  });
});
