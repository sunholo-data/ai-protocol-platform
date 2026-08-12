// TenantOnboardWizard — atomic tenant onboarding via POST /api/admin/tenants.
// Renders each validation step's verdict from the response (NEVER-SILENT: 422
// bad-ref, 409 already-exists, bucket IAM-unreachable warning, and the per-step
// verdict list all render). v6.9.0 M4.
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

interface OnboardResponse {
  domain: string;
  config: ClientConfig;
  validation: TenantValidation;
}

interface Draft {
  domain: string;
  display_name: string;
  documents_bucket: string;
  default_skill: string;
  enabled_skills: string[];
  derived_group_tags: string;
}

const EMPTY: Draft = {
  domain: "",
  display_name: "",
  documents_bucket: "",
  default_skill: "",
  enabled_skills: [],
  derived_group_tags: "",
};

export function TenantOnboardWizard({
  availableSkills,
  onCreated,
  onCancel,
}: {
  availableSkills: SkillOption[];
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<Draft>({ ...EMPTY });
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [badRefs, setBadRefs] = useState<string[]>([]);
  const [result, setResult] = useState<OnboardResponse | null>(null);

  async function submit() {
    const domain = draft.domain.trim().toLowerCase();
    setNotice(null);
    setBadRefs([]);
    if (!domain || !domain.includes(".")) {
      setNotice("Enter a valid email domain (e.g. acme-corp.com).");
      return;
    }
    setSubmitting(true);
    try {
      const body = {
        domain,
        display_name: draft.display_name.trim(),
        documents_bucket: draft.documents_bucket.trim() || null,
        default_skill: draft.default_skill.trim() || null,
        enabled_skills: draft.enabled_skills.length ? draft.enabled_skills : null,
        derived_group_tags: csvToList(draft.derived_group_tags),
      };
      const r = await fetchWithAuth("/api/proxy/api/admin/tenants", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.status === 403) {
        setNotice("You are not an admin for this domain (tenant-admin or aitana-admin required).");
        return;
      }
      if (r.status === 409) {
        setNotice(`Tenant ${domain} already exists — edit it from the list instead.`);
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
        setNotice(`Onboard failed: HTTP ${r.status}`);
        return;
      }
      setResult((await r.json()) as OnboardResponse);
    } catch (e) {
      setNotice(`Onboard failed: ${e instanceof Error ? e.message : "error"}`);
    } finally {
      setSubmitting(false);
    }
  }

  // Terminal success state: show the per-step verdicts (bucket warnings etc.).
  if (result) {
    return (
      <section className="mt-6 rounded-lg border p-4">
        <h2 className="mb-3 text-base font-semibold">Onboarded {result.domain}</h2>
        <ValidationVerdicts validation={result.validation} />
        <div className="mt-4 flex gap-2">
          <button
            className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
            onClick={onCreated}
          >
            Done
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="mb-1 text-base font-semibold">Onboard a new tenant</h2>
      <p className="mb-3 text-sm text-muted-foreground">
        Validates skill references, probes the documents bucket, and creates the tenant in one step.
      </p>

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
        <Field label="Email domain" hint="The doc key, e.g. acme-corp.com">
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={draft.domain}
            onChange={(e) => setDraft({ ...draft, domain: e.target.value })}
            placeholder="acme-corp.com"
          />
        </Field>
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
          </select>
        </Field>
        <Field label="Documents bucket" hint="Per-tenant GCS bucket. Reachability is probed on submit.">
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
          <Field label="Enabled skills" hint="Empty = tenant sees all skills.">
            <SkillMultiSelect
              options={availableSkills}
              selected={draft.enabled_skills}
              onChange={(next) => setDraft({ ...draft, enabled_skills: next })}
            />
          </Field>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
          onClick={() => void submit()}
          disabled={submitting}
        >
          {submitting ? "Onboarding…" : "Onboard tenant"}
        </button>
        <button className="rounded-md border px-3 py-1.5 text-sm" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}
