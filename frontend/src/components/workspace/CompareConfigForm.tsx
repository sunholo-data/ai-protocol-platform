// PPA-COMPARE-LAUNCHER M3 — CompareConfigForm (pre-run scoping)
//
// A compact pre-run config surface rendered INLINE in the CompareLauncher card
// (workbench), NOT in chat. Design-doc Open Question 1 asked "pure A2UI in chat
// vs bespoke React"; the resolution (recorded in the sprint) is bespoke React
// in the workbench card, because `one-doc-compare` is a Model-B skill — the
// model cannot emit an A2UI form into the chat area, and the out-of-model A2UI
// emitter is scoped to tool RESULTS, not pre-run input. So the config lives
// beside the launcher and its output rides the same `start_compare`
// context.config the one-click path already sends.
//
// It lets the user NARROW a comparison before paying the (slow, Pro-tier) diff:
//   * clause multi-select over the 12 standard clause fields
//   * severity focus (material only / +moderate / all)
//   * other-clauses depth
//
// CACHE CORRECTNESS (7.5 caches <-> 7.2-M2 subset runs): a full-scope run MUST
// reuse the legacy cache keys, so the emitted config is the EMPTY object `{}`
// when everything is at its default. Concretely:
//   * all 12 clauses selected      -> the `clauses` key is OMITTED (a subset
//     would key a different cache variant; see extract_ppa_clauses.clause_cache_key)
//   * depth === default (20)       -> `max_other_clauses` is OMITTED (it is part
//     of the cache-variant suffix, so sending 20 explicitly would MISS the
//     legacy full-cap entry)
//   * severity "all"               -> `severity_floor` is OMITTED (narrative-only,
//     not a tool/cache arg, but kept out of the payload when unset for cleanliness)
// Defaults therefore submit `{}`, exactly matching the M2 one-click path.

"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * The 12 standard clause field names. MIRRORED from the source of truth,
 * `backend/tools/schemas/ppa_clauses.py::PpaClauses` (exposed as
 * `STANDARD_CLAUSE_FIELDS` in `backend/tools/extract_ppa_clauses.py`). Keep
 * this list — and its order — in sync with that schema; the backend rejects
 * any clause name outside it (validate_clause_subset).
 */
export const STANDARD_CLAUSE_FIELDS = [
  "counterparty_buyer",
  "counterparty_seller",
  "volume_mwh",
  "term_years",
  "settlement_type",
  "contract_form",
  "price_formula",
  "rtm_provider",
  "force_majeure",
  "change_of_law",
  "termination",
  "governing_law",
] as const;

export type ClauseField = (typeof STANDARD_CLAUSE_FIELDS)[number];

/** Human labels for the checklist. Cosmetic only — the wire value is the
 * snake_case field name above. */
const CLAUSE_LABELS: Record<ClauseField, string> = {
  counterparty_buyer: "Counterparty (Buyer)",
  counterparty_seller: "Counterparty (Seller)",
  volume_mwh: "Volume (MWh)",
  term_years: "Term (years)",
  settlement_type: "Settlement type",
  contract_form: "Contract form",
  price_formula: "Price formula",
  rtm_provider: "Route-to-market provider",
  force_majeure: "Force majeure",
  change_of_law: "Change of law",
  termination: "Termination",
  governing_law: "Governing law",
};

/** Segmented-control choices -> the narrative `severity_floor` value. "all" is
 * the default and emits no `severity_floor` (the diff shows every severity). */
type SeverityChoice = "material" | "moderate" | "all";
const SEVERITY_OPTIONS: { value: SeverityChoice; label: string; floor?: string }[] = [
  { value: "material", label: "Material only", floor: "material" },
  { value: "moderate", label: "+ Moderate", floor: "moderate" },
  { value: "all", label: "All", floor: undefined },
];

/** Default other-clauses depth — mirrors backend PPA_MAX_OTHER_CLAUSES (20).
 * When the user leaves this untouched the key is omitted so the run reuses the
 * legacy full-cap cache entry. */
export const DEFAULT_MAX_OTHER_CLAUSES = 20;
const MAX_OTHER_CEILING = 100;

/** The pre-run config threaded into `start_compare` context.config. Every key
 * is optional; an all-default scope is the empty object. */
export interface CompareConfig {
  clauses?: string[];
  severity_floor?: string;
  max_other_clauses?: number;
}

export interface CompareConfigFormProps {
  /** Current config (so re-opening the form restores the last scope). */
  initialConfig?: CompareConfig;
  /** Apply -> emits the normalised config (empty object when all-default). */
  onSubmit: (config: CompareConfig) => void;
  /** Dismiss without applying. */
  onCancel?: () => void;
}

function severityChoiceFromFloor(floor: string | undefined): SeverityChoice {
  if (floor === "material") return "material";
  if (floor === "moderate") return "moderate";
  return "all";
}

export function CompareConfigForm({
  initialConfig,
  onSubmit,
  onCancel,
}: CompareConfigFormProps) {
  // Seed clause selection: an explicit subset in initialConfig restores it;
  // otherwise ALL clauses are selected (the default full scope).
  const [selected, setSelected] = useState<Set<ClauseField>>(() => {
    const subset = initialConfig?.clauses;
    if (subset && subset.length > 0) {
      return new Set(subset.filter((c): c is ClauseField => STANDARD_CLAUSE_FIELDS.includes(c as ClauseField)));
    }
    return new Set(STANDARD_CLAUSE_FIELDS);
  });
  const [severity, setSeverity] = useState<SeverityChoice>(() =>
    severityChoiceFromFloor(initialConfig?.severity_floor),
  );
  const [maxOther, setMaxOther] = useState<number>(
    () => initialConfig?.max_other_clauses ?? DEFAULT_MAX_OTHER_CLAUSES,
  );

  const allSelected = selected.size === STANDARD_CLAUSE_FIELDS.length;
  const noneSelected = selected.size === 0;

  function toggleClause(field: ClauseField) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set(STANDARD_CLAUSE_FIELDS));
  }
  function clearAll() {
    setSelected(new Set());
  }

  const clampedMaxOther = useMemo(
    () => Math.max(0, Math.min(MAX_OTHER_CEILING, Math.trunc(maxOther) || 0)),
    [maxOther],
  );

  function buildConfig(): CompareConfig {
    const config: CompareConfig = {};
    // Subset only — a full selection omits the key so the run reuses legacy
    // (non-variant) cache entries. Filter through the canonical order so the
    // emitted array is deterministic.
    if (!allSelected) {
      config.clauses = STANDARD_CLAUSE_FIELDS.filter((c) => selected.has(c));
    }
    const floor = SEVERITY_OPTIONS.find((o) => o.value === severity)?.floor;
    if (floor) config.severity_floor = floor;
    // Only a non-default depth is carried (default 20 stays legacy-keyed).
    if (clampedMaxOther !== DEFAULT_MAX_OTHER_CLAUSES) {
      config.max_other_clauses = clampedMaxOther;
    }
    return config;
  }

  function handleApply() {
    if (noneSelected) return; // backend rejects clauses=[]; block the submit.
    onSubmit(buildConfig());
  }

  return (
    <div
      data-testid="compare-config-form"
      className="space-y-4 rounded-md border border-border bg-muted/20 p-4"
    >
      <div className="space-y-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          Scope the comparison
        </p>
        <p className="text-xs text-muted-foreground">
          Narrow what to compare before running — fewer clauses means a faster,
          cheaper diff. Leave everything as-is for a full comparison.
        </p>
      </div>

      {/* Clause multi-select */}
      <fieldset className="space-y-2">
        <div className="flex items-center justify-between">
          <legend className="text-xs font-semibold text-foreground">Clauses</legend>
          <div className="flex items-center gap-2 text-[11px]">
            <button
              type="button"
              onClick={selectAll}
              disabled={allSelected}
              className="text-muted-foreground underline-offset-2 hover:text-primary hover:underline disabled:opacity-40 disabled:hover:no-underline"
            >
              Select all
            </button>
            <span className="text-muted-foreground/40">·</span>
            <button
              type="button"
              onClick={clearAll}
              disabled={noneSelected}
              className="text-muted-foreground underline-offset-2 hover:text-primary hover:underline disabled:opacity-40 disabled:hover:no-underline"
            >
              Clear
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="Clauses to compare">
          {STANDARD_CLAUSE_FIELDS.map((field) => {
            const checked = selected.has(field);
            return (
              <label
                key={field}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded border px-2 py-1.5 text-xs transition-colors",
                  checked
                    ? "border-primary/50 bg-primary/5 text-foreground"
                    : "border-border bg-background text-muted-foreground hover:bg-muted/40",
                )}
              >
                <input
                  type="checkbox"
                  role="checkbox"
                  className="h-3.5 w-3.5 shrink-0 rounded border-muted-foreground/40 text-primary"
                  checked={checked}
                  aria-label={CLAUSE_LABELS[field]}
                  onChange={() => toggleClause(field)}
                />
                <span className="truncate">{CLAUSE_LABELS[field]}</span>
              </label>
            );
          })}
        </div>
        {noneSelected && (
          <p className="text-[11px] text-destructive">
            Select at least one clause (or Select all for a full comparison).
          </p>
        )}
      </fieldset>

      {/* Severity segmented control */}
      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold text-foreground">Severity focus</legend>
        <div
          role="radiogroup"
          aria-label="Severity focus"
          className="inline-flex rounded-md border border-border bg-background p-0.5"
        >
          {SEVERITY_OPTIONS.map((opt) => {
            const active = severity === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={active}
                aria-label={opt.label}
                onClick={() => setSeverity(opt.value)}
                className={cn(
                  "rounded px-3 py-1 text-xs font-medium transition-colors",
                  active
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      {/* Other-clauses depth stepper */}
      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold text-foreground">
          Non-standard clauses depth
        </legend>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Decrease depth"
            onClick={() => setMaxOther((n) => Math.max(0, Math.trunc(n) - 1))}
            disabled={clampedMaxOther <= 0}
            className="flex h-7 w-7 items-center justify-center rounded border border-border bg-background text-sm text-foreground hover:bg-muted/40 disabled:opacity-40"
          >
            &minus;
          </button>
          <input
            type="number"
            aria-label="Non-standard clauses depth"
            min={0}
            max={MAX_OTHER_CEILING}
            value={clampedMaxOther}
            onChange={(e) => setMaxOther(Number(e.target.value))}
            className="h-7 w-16 rounded border border-border bg-background px-2 text-center text-xs text-foreground"
          />
          <button
            type="button"
            aria-label="Increase depth"
            onClick={() => setMaxOther((n) => Math.min(MAX_OTHER_CEILING, Math.trunc(n) + 1))}
            disabled={clampedMaxOther >= MAX_OTHER_CEILING}
            className="flex h-7 w-7 items-center justify-center rounded border border-border bg-background text-sm text-foreground hover:bg-muted/40 disabled:opacity-40"
          >
            +
          </button>
          <span className="text-[11px] text-muted-foreground">
            {clampedMaxOther === DEFAULT_MAX_OTHER_CLAUSES
              ? "default"
              : "capped per run"}
          </span>
        </div>
      </fieldset>

      <div className="flex items-center gap-3 border-t border-border pt-3">
        <button
          type="button"
          onClick={handleApply}
          disabled={noneSelected}
          className="inline-flex items-center rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Apply scope
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            Cancel
          </button>
        )}
        <span className="ml-auto text-[11px] text-muted-foreground">
          {allSelected ? "all clauses" : `${selected.size} / 12 clauses`}
        </span>
      </div>
    </div>
  );
}
