/**
 * No text below 11px on the surfaces people read.
 *
 * The 2026-11-03 UI audit (step 4) set the rule: "nothing below 11px outside
 * the document thumbnails". This test makes it a rule a machine keeps rather
 * than one a reviewer has to remember. Ported from the AIPLA fork, where the
 * same audit found ~50 instances.
 *
 * Exceptions are listed by file with a reason. Keep the list short: a new entry
 * should be a deliberate choice, not a way to make the test pass.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..");
const ROOTS = ["components", "app"];

const ALLOWED: Record<string, string> = {
  "components/document/StructuredDocPreview.tsx": "document thumbnails, the audit's stated exception",
  "components/landing/OneHeroVisual.tsx": "decorative landing illustration; review against the audit",
};

const TINY = /\btext-\[(?:[0-9]|10)(?:\.\d+)?px\]/g;

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "__tests__" && name !== "node_modules") walk(p, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

describe("minimum text size", () => {
  it("no component or page sets text smaller than 11px", () => {
    const offenders: string[] = [];
    for (const root of ROOTS) {
      for (const file of walk(join(SRC, root))) {
        const rel = relative(SRC, file);
        if (rel in ALLOWED) continue;
        const hits = readFileSync(file, "utf8").match(TINY);
        if (hits) offenders.push(`${rel}: ${hits.join(", ")}`);
      }
    }
    expect(offenders, "use text-[11px] or text-xs (12px); see the header comment").toEqual([]);
  });

  it("every exception still exists, so the list cannot rot", () => {
    for (const rel of Object.keys(ALLOWED)) {
      expect(() => statSync(join(SRC, rel)), rel).not.toThrow();
    }
  });
});
