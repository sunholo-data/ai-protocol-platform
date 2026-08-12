// Shared types + helpers + presentational bits for the tenant-admin surfaces
// (TenantEditor + TenantOnboardWizard). Kept in one module so the editor and
// the onboarding wizard render skill selection and validation verdicts
// identically. v6.9.0 domain-tenant-administration.md (M4).
"use client";

import type { ReactNode } from "react";

/** Mirror of backend/db/clients.py ClientConfig (the admin-facing shape). */
export interface ClientConfig {
  domain: string;
  display_name?: string;
  documents_bucket?: string | null;
  enabled_skills?: string[] | null;
  derived_group_tags?: string[] | null;
  default_skill?: string | null;
}

/** A selectable skill — friendly displayName shown, slug is the canonical id
 * written to enabled_skills / default_skill (FRIENDLY-NAMES: present friendly,
 * store the slug). */
export interface SkillOption {
  slug: string;
  displayName: string;
}

export type VerdictLevel = "ok" | "warning" | "error" | "skipped";

/** Mirror of backend/admin/tenants.py ValidationCheck. */
export interface ValidationCheck {
  field: string;
  level: VerdictLevel;
  message: string;
  details?: Record<string, unknown>;
}

/** Mirror of backend/admin/tenants.py TenantValidation. */
export interface TenantValidation {
  domain: string;
  ok: boolean;
  checks: ValidationCheck[];
}

/** Comma-separated text → list | null (empty collapses to null, matching the
 * backend's "[] means clear/unchanged" semantics). */
export function csvToList(s: string): string[] | null {
  const items = s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  return items.length ? items : null;
}

/** Pull the friendly unknown-slug list out of a FastAPI 422 `detail` body so
 * we can name exactly which refs the backend rejected (NEVER-SILENT). */
export function parseUnknownSkillRefs(detail: unknown): string[] {
  if (detail && typeof detail === "object" && "unknown" in detail) {
    const u = (detail as { unknown?: unknown }).unknown;
    if (Array.isArray(u)) return u.filter((x): x is string => typeof x === "string");
  }
  return [];
}

/** Best-effort friendly message from an error/validation `detail` of unknown
 * shape (string, {message}, or FastAPI validation array). */
export function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const m = (detail as { message?: unknown }).message;
    if (typeof m === "string") return m;
  }
  return fallback;
}

// ---------------------------------------------------------------------------
// Presentational bits (shared)
// ---------------------------------------------------------------------------

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

/** Multi-select over the REAL skills list (checkbox list, not free-text). Any
 * currently-selected slug that is NOT in the catalog is still shown (checked,
 * flagged) so editing never silently drops a configured skill. */
export function SkillMultiSelect({
  options,
  selected,
  onChange,
}: {
  options: SkillOption[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const optionSlugs = new Set(options.map((o) => o.slug));
  const orphanSlugs = selected.filter((s) => !optionSlugs.has(s));
  const rows: SkillOption[] = [
    ...options,
    ...orphanSlugs.map((slug) => ({ slug, displayName: `${slug} (not in catalog)` })),
  ];

  function toggle(slug: string, on: boolean) {
    const set = new Set(selected);
    if (on) set.add(slug);
    else set.delete(slug);
    onChange([...set]);
  }

  if (rows.length === 0) {
    // NEVER-SILENT: an empty catalog is a visible notice, not a blank box.
    return (
      <div className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
        No skills available to enable. Leave empty to show the tenant all skills.
      </div>
    );
  }

  return (
    <div className="max-h-48 overflow-y-auto rounded-md border p-2">
      {rows.map((o) => {
        const checked = selected.includes(o.slug);
        const orphan = orphanSlugs.includes(o.slug);
        return (
          <label
            key={o.slug}
            className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-muted/50"
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => toggle(o.slug, e.target.checked)}
            />
            <span className={orphan ? "text-amber-600" : ""}>{o.displayName}</span>
            <span className="ml-auto font-mono text-xs text-muted-foreground">{o.slug}</span>
          </label>
        );
      })}
    </div>
  );
}

const VERDICT_STYLE: Record<VerdictLevel, { icon: string; className: string; label: string }> = {
  ok: { icon: "✓", className: "text-green-600", label: "OK" },
  warning: { icon: "!", className: "text-amber-600", label: "Warning" },
  error: { icon: "✕", className: "text-red-600", label: "Error" },
  skipped: { icon: "–", className: "text-muted-foreground", label: "Skipped" },
};

/** Render each validation step's verdict. Used after onboard (POST response)
 * and by the editor's "Validate" button. NEVER-SILENT: every check — ok,
 * warning (e.g. bucket IAM-unreachable), error (bad ref), skipped — renders. */
export function ValidationVerdicts({ validation }: { validation: TenantValidation }) {
  return (
    <div className="rounded-md border">
      <div
        className={`border-b px-3 py-2 text-sm font-medium ${
          validation.ok ? "text-green-700" : "text-red-700"
        }`}
        role="status"
      >
        {validation.ok
          ? "Validation passed (warnings are non-blocking)."
          : "Validation failed — fix the errors below."}
      </div>
      <ul className="divide-y">
        {validation.checks.map((c, i) => {
          const style = VERDICT_STYLE[c.level] ?? VERDICT_STYLE.skipped;
          return (
            <li key={`${c.field}-${i}`} className="flex items-start gap-2 px-3 py-2 text-sm">
              <span className={`mt-0.5 font-bold ${style.className}`} aria-hidden>
                {style.icon}
              </span>
              <span className="min-w-0">
                <span className="font-medium">{c.field}</span>{" "}
                <span className={`text-xs ${style.className}`}>({style.label})</span>
                <span className="block text-muted-foreground">{c.message}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Inline "unknown skill refs" banner (the PUT/POST 422 bad-ref path). */
export function BadRefNotice({ refs }: { refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
      Unknown skill slug(s): {refs.join(", ")}. Pick from the list or fix the landing skill.
    </div>
  );
}
