// PPA-COMPARE-LAUNCHER M2 / PPA-OBLIGATION 7.6 M3 — launcher (workbench card)
//
// A zero-typing start path for the PPA skills: pick contracts from the open
// document tabs + the skill's welcome example documents, then start work with
// one click. The card offers TWO affordances, gated by capability + selection:
//
//   * "Compare contracts" (one-doc-compare) — needs exactly TWO selected;
//     fires a `start_compare` action.
//   * "Analyze obligations" (one-ppa-expert) — needs exactly ONE selected;
//     fires a `start_obligation_analysis` action with `{doc}` (the mapper asks
//     the user for an effective date via chat when the contract lacks one, so
//     the launcher never has to collect it).
//
// Both clicks fire through the SAME surface-action-run loop that
// `A2UISurfaceMount`'s `triggerOnAction` uses — we reuse the
// `useActionDrivenAgent` hook directly rather than re-implementing the POST/SSE
// plumbing. No chat message is sent; the agent runs a turn and streams back its
// A2UI result surfaces (the obligation success/refusal render via the
// map_ppa_obligations result→A2UI mapping). Which buttons appear is driven by
// the skill's tool set (`allowCompare` / `allowObligations`) — a skill shows
// only the affordances its tools support.
//
// SECURITY (CLAUDE.md hard rule): the launcher only ever forwards *identities*
// — an open tab's `doc_id` or an example's `gs://bucket/object` URL. Both stay
// behind the authed backend. It NEVER constructs or forwards a public
// `storage.googleapis.com` URL — the backend fetches bytes with its own SA
// after re-checking access. The doc list itself is sourced only from authed
// inputs: open session doc tabs and the skill's welcome block (both delivered
// via `/api/proxy`), never a public listing.
//
// Fallback: when the skill is NOT opted into `allow_action_triggered_runs`
// (toolConfigs.a2ui), the button composes today's chat intent instead — a
// "compare these two PPAs …" message that references both docs, sent through
// the existing chat send path (onCompareViaChat).

"use client";

import { useMemo, useState } from "react";
import type { DocTabData } from "@/components/doc-browser/DocTab";
import type { ExampleDocument } from "@/types/skill";
import { useActionDrivenAgent, type ActionRunActivitySink } from "@/hooks/useActionDrivenAgent";
import { CompareConfigForm, type CompareConfig } from "./CompareConfigForm";
import { cn } from "@/lib/utils";

/** The workspace surface the launcher writes its action into (matches the
 * `a2ui_surface_context.workspace.lastAction` slot documented in the
 * one-doc-compare SKILL.md). */
const WORKSPACE_SURFACE = "workspace";
const MAX_SELECTED = 2;

/** A single side of the compare, in the doc_id | gs_url duality the
 * `compare_ppa_contracts` tool accepts. */
export type CompareDocIdentity = { doc_id: string } | { gs_url: string };

interface CompareCandidate {
  /** Stable unique key for selection state + React keys. */
  key: string;
  label: string;
  /** Secondary line (summary / format). */
  sublabel?: string;
  identity: CompareDocIdentity;
  /** Whether this candidate should start selected (seeded from doc-tabs). */
  seedSelected: boolean;
}

export interface CompareLauncherProps {
  /** Current chat session id (the `sessionId ?? agentSessionId` WorkbenchPane
   * already resolves). Threaded into the action-run endpoint URL. */
  sessionId: string | null;
  /** Active skill id — scopes the surface-action-run endpoint. */
  skillId: string;
  /** `toolConfigs.a2ui.allow_action_triggered_runs`. When true the click
   * drives an agent turn via surface-action-run; when false the launcher
   * composes a chat-intent message instead. */
  optedIn: boolean;
  /** Open document tabs (authed session docs) — doc_id identities. Selection
   * seeds from each tab's `included` flag. */
  docTabs: DocTabData[];
  /** Skill welcome example documents (authed /api/skills payload) — gs_url
   * identities. */
  exampleDocuments: ExampleDocument[];
  /** Fallback send path used when `optedIn` is false. */
  onCompareViaChat: (text: string) => void;
  /** Whether the active skill can compare contracts (has
   * `compare_ppa_contracts`). Gates the "Compare contracts" button. Default
   * true (backwards-compatible with the M2 compare-only card). */
  allowCompare?: boolean;
  /** Whether the active skill can analyze obligations (has
   * `map_ppa_obligations`). Gates the "Analyze obligations" button. Default
   * false. */
  allowObligations?: boolean;
  /** Fallback send path for the obligations affordance when `optedIn` is
   * false. Defaults to `onCompareViaChat` when unset. */
  onAnalyzeViaChat?: (text: string) => void;
  /** Open the picked document in the Document tab — parity with the sidebar
   * Library / examples picker (which import-by-reference on pick). The launcher
   * fires the analysis AND opens the doc, so the user sees the contract next to
   * its result instead of only the result. Host resolves a `gs_url` via
   * import-by-reference and a `doc_id` by focusing the already-open tab. */
  onOpenDocument?: (identity: CompareDocIdentity) => void;
  /** Optional notification fired when the user toggles the inline pre-run
   * config form (clause/severity/depth). The form itself is owned by this
   * component (rendered in the launcher card); this callback just lets a host
   * observe the toggle. */
  onConfigure?: () => void;
  /** ACTIVITY-OBS — sink for surfacing the launcher run's live progress into
   * the Activity panel (owned by ChatShell). Threaded straight into
   * `useActionDrivenAgent`. Absent → the run is silent (pre-observability
   * behaviour), so ChatShell always passes it. */
  activitySink?: ActionRunActivitySink;
}

function gsUrl(example: ExampleDocument): string {
  return `gs://${example.bucket}/${example.object}`;
}

function basename(objectPath: string): string {
  const parts = objectPath.split("/");
  return parts[parts.length - 1] ?? objectPath;
}

function describeCandidate(c: CompareCandidate): string {
  // For a bucket example, ground the reference in its gs:// identity so the
  // chat-fallback agent can resolve it via list_bucket_documents. For an open
  // tab, the filename is enough (it's already in context).
  return "gs_url" in c.identity ? `${c.label} (${c.identity.gs_url})` : c.label;
}

function composeChatIntent(left: CompareCandidate, right: CompareCandidate): string {
  return `Compare these two PPA contracts side by side: ${describeCandidate(left)} and ${describeCandidate(right)}. Show the key clause differences with severity.`;
}

function composeAnalyzeIntent(doc: CompareCandidate): string {
  return `Analyze the obligations in this PPA contract: ${describeCandidate(doc)}. Compute the settlement timeline and show who owes what.`;
}

/** Inline working-indicator (CLAUDE.md #8 NEVER SILENT — a click must show it
 * is working, not just grey the button). Matches the app's animate-spin loaders. */
function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
    </svg>
  );
}

export function CompareLauncher({
  sessionId,
  skillId,
  optedIn,
  docTabs,
  exampleDocuments,
  onCompareViaChat,
  onConfigure,
  allowCompare = true,
  allowObligations = false,
  onAnalyzeViaChat,
  onOpenDocument,
  activitySink,
}: CompareLauncherProps) {
  const { triggerAction } = useActionDrivenAgent({
    skillId,
    sessionId: sessionId ?? "",
    activitySink,
  });

  // Build the candidate list: open doc tabs first (they're the "already in
  // hand" contracts), then the skill's example library. Dedup an example that
  // shares a basename with an open tab so the same file isn't offered twice.
  const candidates = useMemo<CompareCandidate[]>(() => {
    const openNames = new Set(docTabs.map((t) => t.filename.toLowerCase()));
    const fromTabs: CompareCandidate[] = docTabs.map((t) => ({
      key: `doc:${t.id}`,
      label: t.filename,
      sublabel: t.format ? t.format.toUpperCase() : undefined,
      identity: { doc_id: t.id },
      seedSelected: t.included,
    }));
    const fromExamples: CompareCandidate[] = exampleDocuments
      .filter((e) => !openNames.has(basename(e.object).toLowerCase()))
      .map((e) => ({
        key: `gs:${gsUrl(e)}`,
        label: e.label,
        sublabel: e.summary ?? undefined,
        identity: { gs_url: gsUrl(e) },
        seedSelected: false,
      }));
    return [...fromTabs, ...fromExamples];
  }, [docTabs, exampleDocuments]);

  // Seed from the doc-tabs bar: the `included` tabs are pre-checked (capped at
  // two). useState initializer runs once — the launcher only mounts in the
  // empty-state, by which point the session's doc tabs are resolved.
  const [selectedKeys, setSelectedKeys] = useState<string[]>(() =>
    candidates
      .filter((c) => c.seedSelected)
      .slice(0, MAX_SELECTED)
      .map((c) => c.key),
  );
  const [isRunning, setIsRunning] = useState(false);
  // CLAUDE.md #8 NEVER SILENT — a run that fails (RUN_ERROR, gate reject,
  // network) must surface a visible message, not a dead button.
  const [error, setError] = useState<string | null>(null);
  // M3 pre-run scoping. `config` seeds the start_compare context.config;
  // an all-default scope stays `{}` so the run reuses legacy cache keys.
  const [showConfig, setShowConfig] = useState(false);
  const [config, setConfig] = useState<CompareConfig>({});

  // Compare needs TWO contracts; obligation analysis needs exactly ONE. An
  // obligations-only skill (one-ppa-expert — allowCompare false) is a
  // single-select picker: cap 1, a "/1" counter, and picking another contract
  // REPLACES the current one (radio semantics). A compare-capable skill keeps
  // the two-select model (analyze, if also allowed, still fires at exactly 1).
  const maxSelected = allowCompare ? MAX_SELECTED : 1;
  const selectionFull = selectedKeys.length >= maxSelected;
  const canCompare = allowCompare && selectedKeys.length === MAX_SELECTED && !isRunning;
  const canAnalyze = allowObligations && selectedKeys.length === 1 && !isRunning;

  function toggle(key: string) {
    setSelectedKeys((prev) => {
      if (prev.includes(key)) return prev.filter((k) => k !== key);
      // Single-select (maxSelected === 1): a new pick replaces the current one
      // rather than being blocked — so the button never dead-ends on "2/1".
      if (prev.length >= maxSelected) {
        return maxSelected === 1 ? [key] : prev; // replace when single-select, else blocked-at-2
      }
      return [...prev, key];
    });
  }

  async function handleCompare() {
    if (selectedKeys.length !== MAX_SELECTED || isRunning) return;
    const left = candidates.find((c) => c.key === selectedKeys[0]);
    const right = candidates.find((c) => c.key === selectedKeys[1]);
    if (!left || !right) return;

    if (!optedIn) {
      // Fallback: skill not opted into action-triggered runs — send today's
      // chat intent, referencing both docs, through the existing chat path.
      onCompareViaChat(composeChatIntent(left, right));
      return;
    }

    setError(null);
    setIsRunning(true);
    try {
      await triggerAction(WORKSPACE_SURFACE, {
        name: "start_compare",
        // `config` is `{}` on the one-click path and carries the scoped subset
        // (clauses / severity_floor / max_other_clauses) once the inline
        // CompareConfigForm has been applied. An all-default scope stays `{}`
        // so the run reuses the legacy (non-variant) extraction/comparison
        // cache keys.
        context: { left: left.identity, right: right.identity, config },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "The comparison run failed. Please try again.");
    } finally {
      setIsRunning(false);
    }
  }

  async function handleAnalyze() {
    if (selectedKeys.length !== 1 || isRunning) return;
    const doc = candidates.find((c) => c.key === selectedKeys[0]);
    if (!doc) return;

    // Load the picked contract into the Document tab (parity with the bucket /
    // Library picker) — analysing from the workspace initial screen should also
    // open the document, not just run the analysis. Independent of the run.
    onOpenDocument?.(doc.identity);

    if (!optedIn) {
      // Fallback: skill not opted into action-triggered runs — send today's
      // chat intent through the existing chat path.
      (onAnalyzeViaChat ?? onCompareViaChat)(composeAnalyzeIntent(doc));
      return;
    }

    setError(null);
    setIsRunning(true);
    try {
      await triggerAction(WORKSPACE_SURFACE, {
        // Fires the one-ppa-expert `start_obligation_analysis` action. Payload
        // is just the doc identity — the mapper asks the user (via chat) for an
        // effective date when the contract lacks one, so the launcher never has
        // to guess or collect one (design-doc {doc, effective_date?}).
        name: "start_obligation_analysis",
        context: { doc: doc.identity },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "The obligation analysis failed. Please try again.");
    } finally {
      setIsRunning(false);
    }
  }

  const hasCandidates = candidates.length > 0;

  return (
    <div
      data-testid="compare-launcher"
      className="flex h-full flex-col gap-4 p-6"
    >
      <div className="space-y-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          {allowCompare && allowObligations
            ? "Analyse contracts"
            : allowObligations
              ? "Analyze obligations"
              : "Compare contracts"}
        </p>
        <h3 className="text-lg font-semibold tracking-tight text-foreground">
          {allowCompare && allowObligations
            ? "Pick contracts to analyse"
            : allowObligations
              ? "Pick a contract to analyse obligations"
              : "Pick two contracts to compare"}
        </h3>
        <p className="text-sm text-muted-foreground">
          {allowObligations && !allowCompare
            ? "Select one PPA below and compute its verified obligation timeline and settlement — no typing needed. The analysis appears here as the assistant works."
            : allowObligations
              ? "Select one PPA to analyse its obligations, or two to compare them side-by-side — no typing needed. Results appear here as the assistant works."
              : "Select two PPAs below and start a side-by-side comparison — no typing needed. Differences appear here as the assistant works."}
        </p>
      </div>

      {showConfig && (
        <CompareConfigForm
          initialConfig={config}
          onSubmit={(next) => {
            setConfig(next);
            setShowConfig(false);
          }}
          onCancel={() => setShowConfig(false)}
        />
      )}

      {hasCandidates ? (
        <ul className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-auto" role="group" aria-label="Contracts to compare">
          {candidates.map((c) => {
            const checked = selectedKeys.includes(c.key);
            // In single-select mode (maxSelected === 1) a new pick replaces the
            // current one, so unchecked rows stay clickable — only the
            // two-select compare model disables further picks once full.
            const disabled = !checked && selectionFull && maxSelected > 1;
            return (
              <li key={c.key}>
                <label
                  className={cn(
                    "flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                    checked
                      ? "border-primary/60 bg-primary/5"
                      : "border-border bg-background hover:bg-muted/40",
                    disabled && "cursor-not-allowed opacity-50 hover:bg-background",
                  )}
                >
                  <input
                    type="checkbox"
                    role="checkbox"
                    className="h-4 w-4 shrink-0 rounded border-muted-foreground/40 text-primary focus-visible:ring-2 focus-visible:ring-primary/40"
                    checked={checked}
                    disabled={disabled}
                    aria-label={c.label}
                    onChange={() => toggle(c.key)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-foreground">
                      {c.label}
                    </span>
                    {c.sublabel && (
                      <span className="block truncate text-xs text-muted-foreground">
                        {c.sublabel}
                      </span>
                    )}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="flex-1 text-sm text-muted-foreground">
          Open two contracts from the sidebar (or upload your own) to compare
          them here.
        </p>
      )}

      <div className="flex items-center gap-3 border-t border-border pt-4">
        {allowCompare && (
          <button
            type="button"
            onClick={handleCompare}
            disabled={!canCompare}
            aria-busy={isRunning}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunning ? (
              <>
                <Spinner />
                Comparing…
              </>
            ) : (
              "Compare contracts"
            )}
          </button>
        )}
        {allowObligations && (
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={!canAnalyze}
            aria-busy={isRunning}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunning ? (
              <>
                <Spinner />
                Analyzing…
              </>
            ) : (
              "Analyze obligations"
            )}
          </button>
        )}
        {allowCompare && (
        <button
          type="button"
          aria-expanded={showConfig}
          onClick={() => {
            // Toggle the inline scoping form in the launcher card (workbench),
            // not a chat surface — one-doc-compare is Model-B, so the model
            // cannot emit an A2UI form in chat. Still notify the optional
            // onConfigure callback so a host can react if it wants.
            setShowConfig((v) => !v);
            onConfigure?.();
          }}
          className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-primary"
        >
          Configure…
        </button>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {selectedKeys.length} / {maxSelected} selected
        </span>
      </div>
      {/* NEVER SILENT (CLAUDE.md #8): while running, tell the user it's working
       * and where to watch; on failure, show the error — never a dead button. */}
      {error ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      ) : isRunning ? (
        <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
          <Spinner />
          Working… live progress is in the <span className="font-medium text-foreground">Activity</span> tab.
        </p>
      ) : null}
    </div>
  );
}
