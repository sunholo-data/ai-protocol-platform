// Tenant-Admin — manage client-org (domain) config in the app instead of by hand
// Firestore edits. Lists tenants over GET /api/admin/clients, and hosts:
//   - TenantOnboardWizard (POST /api/admin/tenants)  — atomic, validated onboard
//   - TenantEditor        (PUT/DELETE /api/admin/clients/{domain})
// The backend enforces admin gating, so this page is gated by auth + a clean 403
// state. enabled_skills / default_skill are picked from the REAL /api/skills
// list (not free-text). v6.9.0 domain-tenant-administration.md (M4).

"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { EffectiveAccessPanel } from "@/components/admin/EffectiveAccessPanel";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { TenantEditor } from "@/components/admin/TenantEditor";
import { TenantOnboardWizard } from "@/components/admin/TenantOnboardWizard";
import type { ClientConfig, SkillOption, TenantValidation } from "@/components/admin/tenantAdmin";
import { HealthBadge, type HealthState, ReachDot } from "@/components/admin/tenantHealth";

// The subset of a /api/skills entry this page needs.
interface SkillLite {
  slug?: string | null;
  displayName?: string;
  name?: string;
}

const PLATFORM_OWNER_UID = "aitana-platform";

type Status =
  | { kind: "loading" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

type Panel =
  | { mode: "none" }
  | { mode: "new" }
  | { mode: "edit"; tenant: ClientConfig }
  | { mode: "access"; tenant: ClientConfig };

export default function TenantAdminPage() {
  const { user, loading: authLoading } = useAuth();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [tenants, setTenants] = useState<ClientConfig[]>([]);
  const [skills, setSkills] = useState<SkillOption[]>([]);
  const [skillNotice, setSkillNotice] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>({ mode: "none" });
  const [notice, setNotice] = useState<string | null>(null);
  const [health, setHealth] = useState<Record<string, HealthState>>({});

  // Fire one validate call per tenant, in parallel, updating each row's badge as
  // it resolves. Best-effort — a failed probe degrades to "—", never blocks the list.
  const loadHealth = useCallback(async (domains: string[]) => {
    setHealth(Object.fromEntries(domains.map((d) => [d, { kind: "loading" } as HealthState])));
    await Promise.all(
      domains.map(async (d) => {
        try {
          const r = await fetchWithAuth(
            `/api/proxy/api/admin/tenants/${encodeURIComponent(d)}/validate`,
          );
          if (!r.ok) {
            setHealth((h) => ({ ...h, [d]: { kind: "error" } }));
            return;
          }
          const validation = (await r.json()) as TenantValidation;
          setHealth((h) => ({ ...h, [d]: { kind: "ready", validation } }));
        } catch {
          setHealth((h) => ({ ...h, [d]: { kind: "error" } }));
        }
      }),
    );
  }, []);

  const loadTenants = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const r = await fetchWithAuth("/api/proxy/api/admin/clients");
      if (r.status === 403) return setStatus({ kind: "forbidden" });
      if (!r.ok) return setStatus({ kind: "error", message: `HTTP ${r.status}` });
      const data: ClientConfig[] = await r.json();
      data.sort((a, b) => a.domain.localeCompare(b.domain));
      setTenants(data);
      setStatus({ kind: "ready" });
      void loadHealth(data.map((t) => t.domain));
    } catch (e) {
      setStatus({ kind: "error", message: e instanceof Error ? e.message : "Failed to load" });
    }
  }, [loadHealth]);

  const loadSkills = useCallback(async () => {
    setSkillNotice(null);
    try {
      const [ownRes, platformRes] = await Promise.all([
        fetchWithAuth("/api/proxy/api/skills"),
        fetchWithAuth(`/api/proxy/api/skills?ownerId=${encodeURIComponent(PLATFORM_OWNER_UID)}`),
      ]);
      if (!ownRes.ok && !platformRes.ok) {
        setSkillNotice("Could not load the skills list — enabled-skills selection is unavailable.");
        return;
      }
      const own: SkillLite[] = ownRes.ok ? await ownRes.json() : [];
      const platform: SkillLite[] = platformRes.ok ? await platformRes.json() : [];
      const bySlug = new Map<string, SkillOption>();
      for (const s of [...own, ...platform]) {
        if (!s.slug) continue; // only slugged skills are enable-able
        if (!bySlug.has(s.slug)) {
          bySlug.set(s.slug, { slug: s.slug, displayName: s.displayName || s.name || s.slug });
        }
      }
      setSkills([...bySlug.values()].sort((a, b) => a.displayName.localeCompare(b.displayName)));
    } catch (e) {
      setSkillNotice(
        `Could not load the skills list: ${e instanceof Error ? e.message : "error"}.`,
      );
    }
  }, []);

  useEffect(() => {
    if (!authLoading && user) {
      void loadTenants();
      void loadSkills();
    }
  }, [authLoading, user, loadTenants, loadSkills]);

  function afterMutation(message: string) {
    setNotice(message);
    setPanel({ mode: "none" });
    void loadTenants();
  }

  if (authLoading || status.kind === "loading") {
    return <Centered>Loading…</Centered>;
  }
  if (!user) return <SignInRequired />;
  if (status.kind === "forbidden") {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Admins only</h1>
          <p className="text-sm text-muted-foreground">
            Tenant administration requires an admin group (<code>aitana-admin</code> or{" "}
            <code>tenant-admin:&lt;domain&gt;</code>). Ask a platform admin to grant it, then reload.
          </p>
        </div>
      </Centered>
    );
  }
  if (status.kind === "error") {
    return (
      <Centered>
        <div className="text-center">
          <p className="mb-3 text-sm text-red-600">Couldn&apos;t load tenants: {status.message}</p>
          <button className="rounded border px-3 py-1.5 text-sm" onClick={() => void loadTenants()}>
            Retry
          </button>
        </div>
      </Centered>
    );
  }

  return (
    <main className="mx-auto max-w-5xl p-6">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Tenant administration</h1>
          <p className="text-sm text-muted-foreground">
            Per-domain config: the documents bucket and its live reachability, which skills a tenant
            sees, where its users land, and the group tags they inherit.
          </p>
        </div>
        <button
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
          onClick={() => {
            setNotice(null);
            setPanel({ mode: "new" });
          }}
        >
          + New tenant
        </button>
      </header>

      {notice && (
        <div className="mb-4 rounded-md border bg-muted/40 px-3 py-2 text-sm" role="status">
          {notice}
        </div>
      )}
      {skillNotice && (
        <div
          className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-700"
          role="status"
        >
          {skillNotice}
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Domain</th>
              <th className="px-3 py-2">Documents bucket</th>
              <th className="px-3 py-2">Health</th>
              <th className="px-3 py-2">Landing skill</th>
              <th className="px-3 py-2">Enabled skills</th>
              <th className="px-3 py-2">Group tags</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {tenants.length === 0 && (
              <tr>
                <td className="px-3 py-4 text-muted-foreground" colSpan={7}>
                  No tenants configured yet.
                </td>
              </tr>
            )}
            {tenants.map((t) => (
              <tr key={t.domain} className="border-t align-top">
                <td className="px-3 py-2 font-medium">
                  {t.domain}
                  {t.display_name ? (
                    <span className="block text-xs font-normal text-muted-foreground">
                      {t.display_name}
                    </span>
                  ) : null}
                </td>
                <td className="px-3 py-2">
                  {t.documents_bucket ? (
                    <span className="inline-flex items-center gap-1.5">
                      <ReachDot state={health[t.domain]} />
                      <span className="font-mono text-xs">{t.documents_bucket}</span>
                    </span>
                  ) : (
                    <Dim>—</Dim>
                  )}
                </td>
                <td className="px-3 py-2">
                  <HealthBadge state={health[t.domain]} />
                </td>
                <td className="px-3 py-2">{t.default_skill || <Dim>—</Dim>}</td>
                <td className="px-3 py-2">
                  {t.enabled_skills?.length ? t.enabled_skills.join(", ") : <Dim>all</Dim>}
                </td>
                <td className="px-3 py-2">
                  {t.derived_group_tags?.length ? t.derived_group_tags.join(", ") : <Dim>—</Dim>}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    className="rounded border px-2 py-1 text-xs"
                    onClick={() => {
                      setNotice(null);
                      setPanel({ mode: "access", tenant: t });
                    }}
                    title="See the skill list a real user in this tenant gets"
                  >
                    What users see
                  </button>
                  <button
                    className="ml-2 rounded border px-2 py-1 text-xs"
                    onClick={() => {
                      setNotice(null);
                      setPanel({ mode: "edit", tenant: t });
                    }}
                  >
                    Edit
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {panel.mode === "new" && (
        <TenantOnboardWizard
          availableSkills={skills}
          onCreated={() => afterMutation("Tenant onboarded.")}
          onCancel={() => setPanel({ mode: "none" })}
        />
      )}
      {panel.mode === "access" && (
        // key by domain so switching tenants remounts with fresh state (no stale carry-over).
        <div className="mt-4" key={`access-${panel.tenant.domain}`}>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold">{panel.tenant.domain}</h2>
            <button className="text-xs underline" onClick={() => setPanel({ mode: "none" })}>
              Close
            </button>
          </div>
          <EffectiveAccessPanel domain={panel.tenant.domain} />
        </div>
      )}
      {panel.mode === "edit" && (
        // key by domain: the editor seeds its draft from `tenant` ONCE (useState
        // initializer), so without a per-domain key, clicking Edit on a second
        // tenant reuses the same instance and shows the FIRST tenant's config.
        <TenantEditor
          key={`edit-${panel.tenant.domain}`}
          tenant={panel.tenant}
          availableSkills={skills}
          onSaved={() => void loadTenants()}
          onDeleted={() => afterMutation(`Deleted ${panel.tenant.domain}.`)}
          onCancel={() => setPanel({ mode: "none" })}
        />
      )}
    </main>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}

function Dim({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>;
}
