"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { sharedRequest } from "@/lib/sharedRequest";
import type { Skill } from "@/types/skill";

/**
 * v6.11.0 — resolve the tenant's default skill id for the skill dropdown.
 *
 * The default is stored tenant-side as `clients/me.default_skill` (a SLUG, e.g.
 * "demo-assistant"); this hook fetches it once per signed-in user and maps it
 * back to the concrete skillId within the caller's `skills` list. The dropdown
 * pins + highlights that skill at the top. Friendly→id resolution
 * (slug→skillId) happens here so the presentation layer only ever deals in the
 * canonical id (CLAUDE.md #9).
 *
 * `uid` is the signed-in user. The tenant config is THEIR tenant's, so it is
 * fetched again when the user changes and shared per user — a different-tenant
 * sign-in on the same tab must not see the previous tenant's default.
 *
 * Degrades to `null` on any failure — the dropdown then just skips the pinned
 * "Default" section (graceful, never blocks the picker).
 */
export function useTenantDefaultSkill(skills: Skill[], uid: string | null): string | null {
  const [defaultSlug, setDefaultSlug] = useState<string | null>(null);

  useEffect(() => {
    setDefaultSlug(null);
    if (!uid) return;
    let cancelled = false;
    (async () => {
      try {
        // Tenant config: one read per minute for every reader on the page.
        const me = await sharedRequest(
          `clients/me:${uid}`,
          async () => {
            const res = await fetchWithAuth("/api/proxy/api/clients/me");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return (await res.json()) as { default_skill?: string | null };
          },
          60_000,
        );
        if (!cancelled) setDefaultSlug(me.default_skill ?? null);
      } catch {
        // Non-fatal — no pinned default section.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [uid]);

  if (!defaultSlug) return null;
  return skills.find((s) => s.slug && s.slug === defaultSlug)?.skillId ?? null;
}
