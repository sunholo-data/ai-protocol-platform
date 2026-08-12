// Guardrail: every artefact that reports state to the model must be
// DUAL-BRIDGED — route model-context through emitModelContext() (which speaks
// window.openai on ChatGPT/Copilot AND ui/update-model-context postMessage on
// SEP-1865 hosts), NOT raw rpcNotify(). This exists because an artefact that
// only posts ui/* renders in ChatGPT but leaves the model blind to every
// interaction — see docs/workshop/protocol-gotchas.md trap #12. Copying an old
// (pre-2026-07) _template reintroduces the bug silently; this test catches it.

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ARTEFACTS = join(__dirname, "..", "artefacts");

interface Artefact {
  name: string;
  html: string;
}

function collectArtefacts(): Artefact[] {
  const out: Artefact[] = [];
  for (const name of readdirSync(ARTEFACTS)) {
    let versions: string[];
    try {
      versions = readdirSync(join(ARTEFACTS, name));
    } catch {
      continue; // not a directory
    }
    for (const v of versions) {
      const file = join(ARTEFACTS, name, v, "index.html");
      if (existsSync(file)) {
        out.push({ name: `${name}/${v}`, html: readFileSync(file, "utf8") });
      }
    }
  }
  return out;
}

const artefacts = collectArtefacts();

describe("artefact cross-host bridge guardrail", () => {
  it("discovers at least one artefact to check", () => {
    expect(artefacts.length).toBeGreaterThan(0);
  });

  for (const a of artefacts) {
    // Only artefacts that report model context need the bridge.
    const reportsContext = a.html.includes("ui/update-model-context");

    describe(a.name, () => {
      it("dual-bridges model-context (has emitModelContext + window.openai)", () => {
        if (!reportsContext) return; // nothing to report → nothing to guard
        expect(
          a.html.includes("emitModelContext"),
          `${a.name}: reports ui/update-model-context but has no emitModelContext() — ` +
            `did you copy a pre-2026-07 _template? See protocol-gotchas.md #12.`,
        ).toBe(true);
        expect(
          a.html.includes("window.openai"),
          `${a.name}: no window.openai branch — the widget will render in ChatGPT ` +
            `but the model stays blind to every interaction. Dual-bridge it (see _template).`,
        ).toBe(true);
      });

      it("routes model-context through emitModelContext(), not raw rpcNotify()", () => {
        if (!reportsContext) return;
        const direct = (
          a.html.match(/rpcNotify\(\s*["']ui\/update-model-context["']/g) || []
        ).length;
        // The single allowed occurrence is the one INSIDE emitModelContext().
        expect(
          direct,
          `${a.name}: ${direct} direct rpcNotify("ui/update-model-context") call(s) — ` +
            `only the one inside emitModelContext() is allowed. Route the rest through it.`,
        ).toBeLessThanOrEqual(1);
      });
    });
  }
});
