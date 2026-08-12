// useAdminScope — ask the backend what admin authority the caller has.
//
// Before v6.16.0 the UI inferred this by probing a DATA endpoint
// (GET /api/admin/clients) and reading 403 as "not an admin". That is why a
// `tenant-admin:{domain}` holder never saw the Admin link at all: the endpoint
// was platform-only, so a perfectly valid tenant admin looked like a nobody.
// It also conflated "not an admin" with "the backend is broken", since both
// arrive as a non-ok response.
//
// GET /api/admin/whoami answers the role question directly and returns 200 with
// scope:"none" for a non-admin — so "no access" and "something failed" stay
// distinguishable, and the UI can render each visibly (never-silent).

"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { getIdToken } from "@/lib/firebase";

export type AdminScopeKind = "platform" | "tenant" | "none";

/** "loading" until the probe resolves; "error" only for a genuine failure. */
export type AdminScopeState = "loading" | "error" | AdminScopeKind;

export interface AdminScope {
  /** Resolved authority, or loading/error. */
  state: AdminScopeState;
  /** Domains a tenant admin administers (empty for platform — it has all). */
  domains: string[];
  /** True for any admin (platform or tenant) — i.e. show the Admin surface. */
  isAdmin: boolean;
  /** True only for aitana-admin — platform-wide surfaces gate on this. */
  isPlatform: boolean;
  /**
   * Force a token refresh and re-probe.
   *
   * Group tags live in the signed JWT, so a just-granted claim does NOT appear
   * until the token rotates (~1h). Without this a newly-appointed admin sees no
   * Admin link, no error, and no explanation — they simply wait, which is the
   * never-silent failure this exists to close.
   */
  recheck: () => Promise<void>;
}

/**
 * Resolve the caller's admin scope.
 *
 * @param enabled Defer the request until it's actually needed (the user menu
 *   only probes when opened). Defaults to true.
 */
export function useAdminScope(enabled: boolean = true): AdminScope {
  const { user, loading } = useAuth();
  const [state, setState] = useState<AdminScopeState>("loading");
  const [domains, setDomains] = useState<string[]>([]);
  const [checked, setChecked] = useState(false);

  const recheck = useCallback(async () => {
    setState("loading");
    try {
      // Force-refresh FIRST: without it the probe re-reads the same stale token
      // and reports the same stale answer, which looks like the grant failed.
      await getIdToken(true);
    } catch {
      // A refresh failure is not fatal — fall through and probe anyway; the
      // probe's own error handling reports whatever actually went wrong.
    }
    setChecked(false);
  }, []);

  useEffect(() => {
    if (!enabled || checked || loading || !user) return;
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/admin/whoami")
      .then(async (r) => {
        if (cancelled) return;
        if (!r.ok) {
          // whoami answers 200 for every authenticated caller, so a non-ok
          // here is a real failure (network, auth, backend) — surface it as
          // "error", never silently as "not an admin".
          setState("error");
          setChecked(true);
          return;
        }
        const body = (await r.json()) as { scope?: string; domains?: string[] };
        const scope = body.scope === "platform" || body.scope === "tenant" ? body.scope : "none";
        setState(scope);
        setDomains(Array.isArray(body.domains) ? body.domains : []);
        setChecked(true);
      })
      .catch(() => {
        if (!cancelled) {
          setState("error");
          setChecked(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, checked, loading, user]);

  return {
    state,
    domains,
    isAdmin: state === "platform" || state === "tenant",
    isPlatform: state === "platform",
    recheck,
  };
}
