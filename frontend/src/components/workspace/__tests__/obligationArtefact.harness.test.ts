// PPA-OBLIGATION 7.6 M3 — obligation artefact jsdom harness (E2E-equivalent).
//
// The M1 artefact runs inside a sandbox iframe with WASM + `unsafe-eval`, which
// a headless CI browser can't exercise cheaply. This harness (the "jsdom
// equivalent" per the M1 precedent) loads the real artefact HTML into jsdom,
// stubs the WASM boot + host bridge + fetch, and drives the M3 additions:
//
//   * host-injected payload replaces the static demo (placeholder banner drops,
//     title neutralised, the injected obligations render);
//   * saved-scenario restore re-applies the user's what-if deltas on boot;
//   * "reset to extracted" emits obligation.reset;
//   * the serve-api engine-placement flag routes the IDENTICAL api.ail boundary
//     over HTTP instead of WASM, with zero engine-call changes.
//
// Browser E2E against the live sandbox stack is DEFERRED-TO-USER (see the
// milestone report for the exact click-path) — jsdom can't run the real WASM.

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ARTEFACT_HTML = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../../../infrastructure/mcp-sandbox/artefacts/ppa-obligation-analysis/v1/index.html",
);

// The obligation artefact is derived from confidential customer contracts and
// is excluded from the public template fork — the harness must skip cleanly
// there instead of failing on the missing file.
const describeArtefact = existsSync(ARTEFACT_HTML) ? describe : describe.skip;

const ASSET_BASE = "/artefacts/ppa-obligation-analysis/v1/assets";

const INJECTED_PAYLOAD = {
  doc_id: "doc-live",
  effectiveDate: "2024-01-01",
  effective_date_source: "provided",
  obligations: [{ id: "COD", deadline: 731, price: 0 }],
  events: [],
  policy: { penPerDay: 500, penCap: 25000, payWithin: 30, cureDays: 30, ratePct: 1, ratePeriod: 30 },
  policy_sources: {
    penPerDay: "default",
    penCap: "default",
    payWithin: "default",
    cureDays: "default",
    ratePct: "default",
    ratePeriod: "default",
  },
  unmapped: [],
  mapped_clauses: ["termination"],
};

// A minimal but parseable settlement report (parseReport handles it).
const REPORT = ["COD effective: deadline=731 price=0", "vendor_owes=0", "client_owes=0", "net: settled"];

// wasm_exec stub: registers a fake Go whose run() installs the AILANG bridge
// so bootEngine + runEngine succeed without a real WASM module.
const WASM_EXEC_STUB = `
  globalThis.Go = class {
    constructor() { this.importObject = {}; }
    run() {
      globalThis.ailangLoadModule = function () { return { success: true }; };
      globalThis.ailangCall = function () { return { success: true, result: ${JSON.stringify(REPORT)} }; };
    }
  };
`;

interface Captured {
  method?: string;
  id?: number;
  params?: Record<string, unknown>;
}

let posted: Captured[];
let fetchCalls: string[];

function loadArtefact() {
  const html = readFileSync(ARTEFACT_HTML, "utf8");
  const bodyInner = html.match(/<body[^>]*>([\s\S]*)<\/body>/)![1];
  document.body.innerHTML = bodyInner; // script tags land inert
  const scripts = Array.from(document.querySelectorAll("script"));
  const main = scripts.find((s) => !s.getAttribute("type"))!.textContent!;
  // eslint-disable-next-line no-new-func
  new Function(main)();
}

/** Respond to the artefact's ui/initialize request so _initialized flips and
 *  queued model-context notifications flush. */
function ackInitialize() {
  const init = posted.find((m) => m.method === "ui/initialize");
  const id = init?.id ?? 1;
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { jsonrpc: "2.0", id, result: { hostContext: { theme: "light" } } },
    }),
  );
}

function inject(params: Record<string, unknown>) {
  window.dispatchEvent(
    new MessageEvent("message", {
      data: { jsonrpc: "2.0", method: "ui/obligation/payload", params },
    }),
  );
}

function modelContexts() {
  return posted
    .filter((m) => m.method === "ui/update-model-context")
    .map((m) => (m.params?.structuredContent ?? {}) as Record<string, unknown>);
}

beforeEach(() => {
  posted = [];
  fetchCalls = [];
  // Capture host-bound postMessages (window.parent === window in jsdom top).
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetchCalls.push(String(url));
      if (String(url).endsWith("/wasm_exec.js")) {
        return { ok: true, text: async () => WASM_EXEC_STUB } as unknown as Response;
      }
      if (String(url).endsWith("/ailang.wasm")) {
        return { ok: true, arrayBuffer: async () => new ArrayBuffer(8) } as unknown as Response;
      }
      if (String(url).endsWith("/payload.demosolar.json")) {
        return { ok: true, json: async () => INJECTED_PAYLOAD } as unknown as Response;
      }
      // serve-api endpoint
      return { ok: true, json: async () => ({ report: REPORT }) } as unknown as Response;
    }),
  );
  // Fake WASM instantiate so go.run has something to run.
  vi.stubGlobal("WebAssembly", {
    ...WebAssembly,
    instantiate: vi.fn(async () => ({ instance: {}, module: {} })),
  });
  window.parent.postMessage = ((msg: Captured) => {
    posted.push(msg);
  }) as typeof window.parent.postMessage;
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  // Drop the AILANG bridge the stub installed so the next case boots clean.
  // @ts-expect-error test cleanup
  delete globalThis.ailangLoadModule;
  // @ts-expect-error test cleanup
  delete globalThis.ailangCall;
  // @ts-expect-error test cleanup
  delete globalThis.Go;
});

describeArtefact("obligation artefact — injected payload (WASM path)", () => {
  it("boots from the injected payload, drops the demo banner, and renders the settlement", async () => {
    loadArtefact();
    ackInitialize();
    inject({ payload: INJECTED_PAYLOAD });

    await vi.waitFor(() => {
      expect(document.getElementById("status")!.textContent).toContain("engine ready");
    });

    // Demo placeholder banner is hidden; DemoSolar-specific title neutralised.
    expect(document.getElementById("placeholder-banner")!.style.display).toBe("none");
    expect(document.getElementById("title-h1")!.textContent).toBe("Obligation Analysis");
    // The settlement panel rendered (net line present).
    expect(document.getElementById("settlement")!.textContent).toContain("Net");
    // A what-if control exists for the injected obligation.
    expect(document.getElementById("r-dd-COD")).not.toBeNull();
    // WASM path: the wasm assets were fetched, no serve-api call.
    expect(fetchCalls.some((u) => u.endsWith("/ailang.wasm"))).toBe(true);
    // A recompute model-context was emitted with the scenario slot.
    const rc = modelContexts().find((c) => c.kind === "obligation.recompute");
    expect(rc).toBeTruthy();
    expect(rc!.scenario).toBeTruthy();
  });

  it("restores a saved what-if scenario onto the controls", async () => {
    loadArtefact();
    ackInitialize();
    inject({ payload: INJECTED_PAYLOAD, savedScenario: { deadlineDelta: { COD: 45 } } });

    await vi.waitFor(() => {
      expect(document.getElementById("status")!.textContent).toContain("engine ready");
    });
    const slider = document.getElementById("r-dd-COD") as HTMLInputElement;
    expect(slider.value).toBe("45");
  });

  it("emits obligation.reset when Reset to extracted is clicked", async () => {
    loadArtefact();
    ackInitialize();
    inject({ payload: INJECTED_PAYLOAD, savedScenario: { deadlineDelta: { COD: 45 } } });
    await vi.waitFor(() => {
      expect(document.getElementById("status")!.textContent).toContain("engine ready");
    });

    posted.length = 0;
    (document.getElementById("reset") as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(modelContexts().some((c) => c.kind === "obligation.reset")).toBe(true);
    });
    // Reset zeroed the deadline slider back to the extracted value.
    expect((document.getElementById("r-dd-COD") as HTMLInputElement).value).toBe("0");
  });
});

describeArtefact("obligation artefact — reviewed settings (design open question 2)", () => {
  // A base payload with a real unmapped reason for cureDays (drives the help
  // text) so the panel renders provenance + basis like the DemoCorp demo.
  const REVIEW_BASE = {
    ...INJECTED_PAYLOAD,
    unmapped: [
      { clause: "cureDays", reason: "Clause 14 states cure periods in Business Days (10/20/30)." },
      { clause: "price_formula", reason: "Price is a Spot-indexed formula, not a fixed amount." },
    ],
  };

  async function bootReview(params: Record<string, unknown>) {
    loadArtefact();
    ackInitialize();
    inject(params);
    await vi.waitFor(() => {
      expect(document.getElementById("status")!.textContent).toContain("engine ready");
    });
  }

  function badge(dataKey: string): string {
    const row = document.querySelector(`.rv-row[data-key="${dataKey}"]`);
    return row?.querySelector(".rv-badge")?.textContent ?? "";
  }

  it("renders the six knobs + obligation price with provenance badges and basis help", async () => {
    await bootReview({ payload: REVIEW_BASE });
    expect(document.getElementById("reviewed-panel")!.style.display).not.toBe("none");
    // Six policy rows + one price row.
    for (const k of ["penPerDay", "penCap", "payWithin", "cureDays", "ratePct", "ratePeriod"]) {
      expect(badge(k)).toBe("default");
    }
    expect(badge("COD")).toBe("default");
    // The unmapped reason renders as the per-knob basis help text.
    expect(document.getElementById("reviewed-body")!.textContent).toContain(
      "Clause 14 states cure periods in Business Days",
    );
  });

  it("shows an extracted knob read-only (no input, locked note)", async () => {
    const base = { ...REVIEW_BASE, policy_sources: { ...REVIEW_BASE.policy_sources, penPerDay: "extracted" } };
    await bootReview({ payload: base });
    expect(badge("penPerDay")).toBe("extracted");
    const row = document.querySelector('.rv-row[data-key="penPerDay"]')!;
    expect(row.querySelector("input")).toBeNull(); // read-only — no editor
    expect(row.textContent).toContain("locked");
  });

  it("editing a default knob flips it to reviewed, recomputes, and emits the reviewed overlay", async () => {
    await bootReview({ payload: REVIEW_BASE });
    posted.length = 0;

    const input = document.querySelector('input[data-rvknob="cureDays"]') as HTMLInputElement;
    input.value = "28";
    input.dispatchEvent(new Event("change"));

    await vi.waitFor(() => expect(badge("cureDays")).toBe("reviewed"));
    // The canonical policy the engine sees now carries the reviewed value.
    const rc = modelContexts().filter((c) => c.kind === "obligation.recompute").at(-1)!;
    expect(rc.reviewed).toEqual({ policy: { cureDays: 28 } });
  });

  it("editing an obligation price flips its provenance and carries into the overlay", async () => {
    await bootReview({ payload: REVIEW_BASE });
    posted.length = 0;
    const input = document.querySelector('input[data-rvprice="COD"]') as HTMLInputElement;
    input.value = "5000000";
    input.dispatchEvent(new Event("change"));

    await vi.waitFor(() => expect(badge("COD")).toBe("reviewed"));
    const rc = modelContexts().filter((c) => c.kind === "obligation.recompute").at(-1)!;
    expect(rc.reviewed).toEqual({ obligation_prices: { COD: 5000000 } });
  });

  it("rehydrates a prior review from the injected reviewedSettings", async () => {
    await bootReview({
      payload: REVIEW_BASE,
      reviewedSettings: { policy: { penPerDay: 5000 }, obligation_prices: { COD: 5000000 } },
    });
    expect(badge("penPerDay")).toBe("reviewed");
    expect(badge("COD")).toBe("reviewed");
    const input = document.querySelector('input[data-rvknob="penPerDay"]') as HTMLInputElement;
    expect(input.value).toBe("5000");
  });

  it("reset to extracted/default clears every reviewed value", async () => {
    await bootReview({ payload: REVIEW_BASE, reviewedSettings: { policy: { penPerDay: 5000 } } });
    expect(badge("penPerDay")).toBe("reviewed");
    posted.length = 0;

    (document.getElementById("reviewed-reset") as HTMLAnchorElement).click();

    await vi.waitFor(() => expect(badge("penPerDay")).toBe("default"));
    const rc = modelContexts().filter((c) => c.kind === "obligation.recompute").at(-1)!;
    expect(rc.reviewed).toEqual({}); // empty overlay = identity
  });
});

describeArtefact("obligation artefact — serve-api engine placement (fallback flag)", () => {
  it("routes the identical boundary over HTTP and never boots WASM when engine.url is set", async () => {
    const SERVE_API = "https://deontic.example/analyze";
    loadArtefact();
    ackInitialize();
    inject({ payload: INJECTED_PAYLOAD, engine: { mode: "serve-api", url: SERVE_API } });

    await vi.waitFor(() => {
      expect(document.getElementById("status")!.textContent).toContain("serve-api");
    });

    // The scenario was POSTed to the serve-api URL; WASM was never fetched.
    expect(fetchCalls).toContain(SERVE_API);
    expect(fetchCalls.some((u) => u.endsWith("/ailang.wasm"))).toBe(false);
    // Same report → settlement still renders.
    expect(document.getElementById("settlement")!.textContent).toContain("Net");
  });
});
