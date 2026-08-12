// /dev/a2ui — A2UI fixture playground.
//
// A standalone dev page that hosts hand-curated A2UI scenarios so we can
// exercise each rendering pattern without needing a real agent run. The
// page wires the SurfaceRegistryProvider itself (the global layout only
// provides AuthProvider), so each Pattern section can mount one or more
// `<A2UISurfaceMount>` components with whatever skill/session ids it
// needs.
//
// ── ACTION-TRIGGER M3.1 (sprint 1.21) ──
// The Pattern 1 — Click-Driven section mounts a single A2UI surface with
// `triggerOnAction={true}`. The hand-crafted A2UI message seeds a
// Button-with-counter shape that matches what the `demo-click-counter`
// LOCAL_MODE skill emits, so the visual look matches whether the page
// is opened against a live backend or in a vitest. Each click POSTs to
// `/api/skills/demo-click-counter/sessions/.../surface-action-run` — the
// agent re-emits an updated surface; no chat bubble appears.

"use client";

import { useEffect, useState } from "react";
import { basicCatalog } from "@a2ui/react/v0_9";
import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { fetchWithAuth } from "@/lib/apiClient";
import {
  SurfaceRegistryProvider,
  useSurfaceRegistry,
} from "@/providers/SurfaceRegistry";

// Shared contract (sprint 1.21 ACTION-TRIGGER M3 — pinned in the sprint
// plan, mirrored by the CLI subcommand and the smoke script).
const PATTERN1_SKILL_ID = "demo-click-counter";
const PATTERN1_SURFACE_ID = "counter-main";
const PATTERN1_SESSION_ID = "pattern1-fixture-001";

/**
 * Hand-crafted A2UI v0.9 payload that mirrors what the `demo-click-counter`
 * agent emits on its first turn. Pushed into the SurfaceRegistry directly
 * so the fixture page renders the Button even when no agent is running.
 *
 * Wire format reference: `backend/db/local_fixture.py` -> demo-click-counter
 * skill instructions. The two stay in lock-step deliberately: the click on
 * this rendered Button fires action `increment` whether it came from the
 * fixture seed or a live agent turn.
 */
const PATTERN1_SEED_MESSAGES: Record<string, unknown>[] = [
  {
    // v0.9 `createSurface` declares ONLY surfaceId + catalogId — the SDK's
    // MessageProcessor ingests components exclusively via `updateComponents`
    // (see @a2ui/web_core message-processor: createSurface never reads a
    // `components` field). Emitting components inline here silently drops them,
    // so the surface renders "[Loading root...]" forever. The root component id
    // is "root" by convention (A2uiSurface renders DeferredChild id="root").
    version: "v0.9",
    createSurface: {
      surfaceId: PATTERN1_SURFACE_ID,
      catalogId: basicCatalog.id,
    },
  },
  {
    version: "v0.9",
    updateComponents: {
      surfaceId: PATTERN1_SURFACE_ID,
      components: [
        {
          id: "root",
          component: "Column",
          children: ["title", "display", "btn"],
        },
        {
          id: "title",
          component: "Text",
          text: "Click Counter",
          variant: "h2",
        },
        {
          id: "display",
          component: "Text",
          text: { path: "/counterDisplay" },
        },
        {
          id: "btn",
          component: "Button",
          child: "btn-label",
          action: {
            event: {
              name: "increment",
              context: {},
            },
          },
        },
        {
          id: "btn-label",
          component: "Text",
          text: "Click me",
        },
      ],
    },
  },
  {
    // v0.9 `updateDataModel` sets ONE path to a value: { surfaceId, path, value }.
    // (Not { surfaceId, data: {...} } — the processor reads payload.path/value,
    // so a `data` blob is silently ignored and bindings resolve to nothing.)
    version: "v0.9",
    updateDataModel: {
      surfaceId: PATTERN1_SURFACE_ID,
      path: "/",
      value: {
        counter: 0,
        counterDisplay: "Clicks: 0",
      },
    },
  },
];

/**
 * One-shot effect: when the surface registry first mounts, seed the
 * Pattern 1 surface so the fixture page is visually complete on first
 * paint. Idempotent — the registry's `consumedToolCallIds` guard absorbs
 * double-effects (e.g. React 19 Strict Mode).
 */
function Pattern1Seeder() {
  const registry = useSurfaceRegistry();
  useEffect(() => {
    registry.appendMessages(
      PATTERN1_SURFACE_ID,
      PATTERN1_SEED_MESSAGES,
      "pattern1-seed-001",
    );
  }, [registry]);
  return null;
}

function Pattern1Section() {
  // Local state so manual page-driven smoke tests can see whether the
  // backend is reachable — the `useActionDrivenAgent` hook resolves
  // cleanly on 4xx, so without surfacing something the click looks like
  // a no-op.
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    // Reset hint when surface re-renders so the next click can update it.
    if (!hint) return;
    const t = setTimeout(() => setHint(null), 4000);
    return () => clearTimeout(t);
  }, [hint]);

  // Pre-create the ADK session so the click-driven surface-action-run has a
  // session to write to + run against. The chat path bootstraps automatically
  // (ChatShell); a fixture that fires actions without ever chatting must do it
  // explicitly, else the first click 404s ("Session not found"). Idempotent.
  useEffect(() => {
    void fetchWithAuth(
      `/api/proxy/api/sessions/${PATTERN1_SESSION_ID}/bootstrap`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_id: PATTERN1_SKILL_ID }),
      },
    ).catch(() => {});
  }, []);

  return (
    <section className="space-y-3" data-testid="pattern1-section">
      <header className="space-y-1">
        <h2 className="text-lg font-semibold">Pattern 1 — Click-Driven AI UI</h2>
        <p className="text-sm text-muted-foreground">
          This surface fires an agent turn on click — no chat composer.
        </p>
      </header>
      <div className="rounded border p-3">
        <A2UISurfaceMount
          surfaceId={PATTERN1_SURFACE_ID}
          skillId={PATTERN1_SKILL_ID}
          sessionId={PATTERN1_SESSION_ID}
          triggerOnAction={true}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Action body POSTs to{" "}
        <code>
          /api/skills/{PATTERN1_SKILL_ID}/sessions/{PATTERN1_SESSION_ID}
          /surface-action-run
        </code>
        . The agent re-emits an updated A2UI surface; no chat bubble
        appears.
      </p>
      {hint && (
        <p
          className="text-xs text-amber-700"
          role="status"
          data-testid="pattern1-hint"
        >
          {hint}
        </p>
      )}
    </section>
  );
}

export default function A2uiDevPage() {
  return (
    <SurfaceRegistryProvider>
      <main className="mx-auto max-w-3xl space-y-8 p-8">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold">A2UI fixture playground</h1>
          <p className="text-sm text-muted-foreground">
            Hand-crafted A2UI scenarios that render without a live agent.
            Useful when triaging surface-rendering bugs without booting the
            whole backend.
          </p>
        </header>
        <Pattern1Seeder />
        <Pattern1Section />
      </main>
    </SurfaceRegistryProvider>
  );
}
