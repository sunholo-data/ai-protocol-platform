// EffectiveAccessPanel — "what your users actually see" (v6.16.0 Phase 2).
//
// Answers the question that motivated the whole admin sprint: an admin looking
// at their own skill list is looking at a STRICTLY WIDER set than their users
// get, because skill-admins bypass the tenant's enabled_skills narrowing. There
// was no way to tell which entries an ordinary user would lose — so the tenant
// screen appeared to list skills ONE's users don't have.
//
// Every verdict and reason string here comes from the backend
// (POST /api/admin/access/check with includeSkills). This component deliberately
// does NOT re-derive access: a second implementation would drift from
// enforcement, and the backend already routes both this and GET /api/skills
// through one evaluator so they cannot disagree. (Axiom #10 — thin client.)

"use client";

import { useCallback, useState } from "react";

import { fetchWithAuth } from "@/lib/apiClient";

interface SkillRow {
  skillId: string;
  slug: string;
  label: string;
  visible: boolean;
  reason: string;
  accessAllowed: boolean;
  tenantAllowed: boolean;
  adminBypass: boolean;
}

interface VisibilityPlane {
  enabledSkills: string[] | null;
  visibleCount: number;
  totalCount: number;
  hiddenByTenantFilter: string[];
  skills: SkillRow[];
}

interface CheckResponse {
  email: string;
  domain: string;
  userFound?: boolean;
  user_found?: boolean;
  skillVisibility?: VisibilityPlane | null;
}

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; email: string; userFound: boolean; plane: VisibilityPlane };

export function EffectiveAccessPanel({ domain }: { domain: string }) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const run = useCallback(
    async (target: string) => {
      const addr = target.trim();
      if (!addr) return;
      setState({ kind: "loading" });
      try {
        const r = await fetchWithAuth("/api/proxy/api/admin/access/check", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: addr, includeSkills: true }),
        });
        if (!r.ok) {
          // Never-silent: name the likely cause rather than failing blank.
          const detail =
            r.status === 403
              ? `${addr} is outside your tenant scope — you can only inspect users in ${domain}.`
              : `The access check failed (HTTP ${r.status}).`;
          setState({ kind: "error", message: detail });
          return;
        }
        const body = (await r.json()) as CheckResponse;
        if (!body.skillVisibility) {
          setState({ kind: "error", message: "The backend returned no visibility data for this user." });
          return;
        }
        setState({
          kind: "ready",
          email: body.email || addr,
          userFound: Boolean(body.userFound ?? body.user_found),
          plane: body.skillVisibility,
        });
      } catch {
        setState({ kind: "error", message: "Couldn't reach the admin service. This is a connection problem." });
      }
    },
    [domain],
  );

  return (
    <div className="rounded-lg border border-border p-4">
      <h3 className="text-sm font-semibold text-foreground">What your users actually see</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Your own skill list isn&apos;t a preview of theirs — admins bypass the tenant&apos;s enabled-skills
        filter. Check a specific user to see their real list.
      </p>

      <form
        className="mt-3 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run(email);
        }}
      >
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder={`someone@${domain}`}
          aria-label="User email to inspect"
          className="min-w-0 flex-1 rounded border border-border bg-background px-2 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={!email.trim() || state.kind === "loading"}
          className="rounded border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
        >
          {state.kind === "loading" ? "Checking…" : "Check"}
        </button>
      </form>

      {state.kind === "error" && (
        <p className="mt-3 rounded border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {state.message}
        </p>
      )}

      {state.kind === "ready" && <Result state={state} />}
    </div>
  );
}

function Result({ state }: { state: Extract<State, { kind: "ready" }> }) {
  const { plane, email, userFound } = state;
  const hidden = plane.hiddenByTenantFilter;

  return (
    <div className="mt-3 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
        <span className="font-medium text-foreground">
          {plane.visibleCount} of {plane.totalCount} skills visible to {email}
        </span>
        {!userFound && (
          // Meaningful distinction: an address that has never signed in has no
          // direct claims, so the answer is a projection, not a fact.
          <span className="text-amber-600 dark:text-amber-500">
            this user hasn&apos;t signed in yet — shown from domain rules only
          </span>
        )}
      </div>

      {plane.enabledSkills === null ? (
        <p className="text-xs text-muted-foreground">
          This tenant has no enabled-skills filter, so users see everything access control allows.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Tenant filter active: <code className="text-[11px]">{plane.enabledSkills.join(", ") || "(empty)"}</code>
        </p>
      )}

      {hidden.length > 0 && (
        // The headline answer to "why does my list show more than theirs?".
        <p className="rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-500">
          You can see {hidden.length} skill{hidden.length === 1 ? "" : "s"} that this user cannot:{" "}
          <span className="font-medium">{hidden.join(", ")}</span>. Add {hidden.length === 1 ? "it" : "them"} to
          the tenant&apos;s enabled skills if that&apos;s not intended.
        </p>
      )}

      {plane.skills.length === 0 ? (
        <p className="text-xs text-muted-foreground">No skills were returned.</p>
      ) : (
        <ul className="divide-y divide-border rounded border border-border">
          {plane.skills.map((s) => (
            <li key={s.skillId || s.slug} className="flex items-start gap-3 px-3 py-2">
              <span
                aria-hidden
                className={
                  "mt-1 h-2 w-2 shrink-0 rounded-full " + (s.visible ? "bg-emerald-500" : "bg-muted-foreground/40")
                }
              />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span
                    className={"text-sm " + (s.visible ? "text-foreground" : "text-muted-foreground line-through")}
                  >
                    {s.label || s.slug}
                  </span>
                  <span className="text-[11px] text-muted-foreground">{s.visible ? "visible" : "hidden"}</span>
                  {s.adminBypass && !s.tenantAllowed && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-500">
                      you only see this as an admin
                    </span>
                  )}
                </div>
                {/* Reason text is authored by the backend — do not synthesise it here. */}
                <p className="mt-0.5 text-xs text-muted-foreground">{s.reason}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
