// TenantEditor — edit an EXISTING tenant's clients/{domain} config via
// PUT /api/admin/clients/{domain}, with a dry-run "Validate" button hitting
// GET /api/admin/tenants/{domain}/validate. Extracted from the inline editor in
// app/admin/tenants/page.tsx (v6.9.0 M4). enabled_skills is a multi-select over
// the REAL /api/skills list (not free-text); API 422 bad-ref and bucket
// IAM-unreachable verdicts render inline (NEVER-SILENT).
"use client";

import { useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import {
  BadRefNotice,
  ClientConfig,
  csvToList,
  detailMessage,
  Field,
  parseUnknownSkillRefs,
  SkillMultiSelect,
  SkillOption,
  TenantValidation,
  ValidationVerdicts,
} from "./tenantAdmin";

interface Draft {
  display_name: string;
  documents_bucket: string;
  default_skill: string;
  enabled_skills: string[];
  derived_group_tags: string; // comma-separated (no registry to pick from yet)
}

function toDraft(c: ClientConfig): Draft {
  return {
    display_name: c.display_name ?? "",
    documents_bucket: c.documents_bucket ?? "",
    default_skill: c.default_skill ?? "",
    enabled_skills: c.enabled_skills ?? [],
    derived_group_tags: (c.derived_group_tags ?? []).join(", "),
  };
}

export function TenantEditor({
  tenant,
  availableSkills,
  onSaved,
  onCancel,
  onDeleted,
}: {
  tenant: ClientConfig;
  availableSkills: SkillOption[];
  onSaved: () => void;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(tenant));
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [badRefs, setBadRefs] = useState<string[]>([]);
  const [validation, setValidation] = useState<TenantValidation | null>(null);

  async function save() {
    setSaving(true);
    setNotice(null);
    setBadRefs([]);
    try {
      const body = {
        display_name: draft.display_name.trim(),
        documents_bucket: draft.documents_bucket.trim() || null,
        default_skill: draft.default_skill.trim() || null,
        enabled_skills: draft.enabled_skills.length ? draft.enabled_skills : null,
        derived_group_tags: csvToList(draft.derived_group_tags),
      };
      const r = await fetchWithAuth(
        `/api/proxy/api/admin/clients/${encodeURIComponent(tenant.domain)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (r.status === 403) {
        setNotice("You need the aitana-admin group to change tenant config.");
        return;
      }
      if (r.status === 422) {
        const data = await r.json().catch(() => ({}));
        const refs = parseUnknownSkillRefs(data?.detail);
        setBadRefs(refs);
        setNotice(refs.length ? null : detailMessage(data?.detail, "Validation failed."));
        return;
      }
      if (!r.ok) {
        setNotice(`Save failed: HTTP ${r.status}`);
        return;
      }
      // Refresh the list (updates the overview's bucket/health badges), then
      // auto-validate the now-stored config so a newly-set bucket is checked for
      // SA access right here — no separate Validate click needed.
      onSaved();
      await validate();
      setNotice("Saved ✓ — config re-checked below.");
    } catch (e) {
      setNotice(`Save failed: ${e instanceof Error ? e.message : "error"}`);
    } finally {
      setSaving(false);
    }
  }

  async function validate() {
    setValidating(true);
    setNotice(null);
    setValidation(null);
    try {
      const r = await fetchWithAuth(
        `/api/proxy/api/admin/tenants/${encodeURIComponent(tenant.domain)}/validate`,
      );
      if (r.status === 403) {
        setNotice("You are not an admin for this tenant.");
        return;
      }
      if (r.status === 404) {
        setNotice("Save the tenant first, then re-validate.");
        return;
      }
      if (!r.ok) {
        setNotice(`Validation failed: HTTP ${r.status}`);
        return;
      }
      setValidation((await r.json()) as TenantValidation);
    } catch (e) {
      setNotice(`Validation failed: ${e instanceof Error ? e.message : "error"}`);
    } finally {
      setValidating(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete tenant config for ${tenant.domain}? This cannot be undone.`)) return;
    setNotice(null);
    try {
      const r = await fetchWithAuth(
        `/api/proxy/api/admin/clients/${encodeURIComponent(tenant.domain)}`,
        { method: "DELETE" },
      );
      if (!r.ok && r.status !== 404) {
        setNotice(`Delete failed: HTTP ${r.status}`);
        return;
      }
      onDeleted();
    } catch (e) {
      setNotice(`Delete failed: ${e instanceof Error ? e.message : "error"}`);
    }
  }

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="mb-3 text-base font-semibold">Edit {tenant.domain}</h2>

      {notice && (
        <div className="mb-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" role="status">
          {notice}
        </div>
      )}
      {badRefs.length > 0 && (
        <div className="mb-3">
          <BadRefNotice refs={badRefs} />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Display name">
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={draft.display_name}
            onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
          />
        </Field>
        <Field label="Landing skill (default_skill)" hint="Skill users land on with no prior chat.">
          <select
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={draft.default_skill}
            onChange={(e) => setDraft({ ...draft, default_skill: e.target.value })}
          >
            <option value="">— marketplace default —</option>
            {availableSkills.map((s) => (
              <option key={s.slug} value={s.slug}>
                {s.displayName} ({s.slug})
              </option>
            ))}
            {draft.default_skill &&
              !availableSkills.some((s) => s.slug === draft.default_skill) && (
                <option value={draft.default_skill}>{draft.default_skill} (not in catalog)</option>
              )}
          </select>
        </Field>
        <Field label="Documents bucket" hint="Per-tenant GCS bucket. Validate checks SA read access.">
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={draft.documents_bucket}
            onChange={(e) => setDraft({ ...draft, documents_bucket: e.target.value })}
          />
        </Field>
        <Field label="Derived group tags" hint="Comma-separated. Granted to EVERY user of this domain.">
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={draft.derived_group_tags}
            onChange={(e) => setDraft({ ...draft, derived_group_tags: e.target.value })}
            placeholder="ACME"
          />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Enabled skills" hint="Empty = tenant sees all skills. Admins bypass this filter.">
            <SkillMultiSelect
              options={availableSkills}
              selected={draft.enabled_skills}
              onChange={(next) => setDraft({ ...draft, enabled_skills: next })}
            />
          </Field>
        </div>
      </div>

      {validation && (
        <div className="mt-4">
          <ValidationVerdicts validation={validation} />
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save"}
        </button>
        <button
          className="rounded-md border px-3 py-1.5 text-sm disabled:opacity-60"
          onClick={() => void validate()}
          disabled={validating}
        >
          {validating ? "Validating…" : "Validate"}
        </button>
        <button className="rounded-md border px-3 py-1.5 text-sm" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="ml-auto rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600"
          onClick={() => void remove()}
        >
          Delete tenant
        </button>
      </div>
    </section>
  );
}
