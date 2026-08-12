"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { Skill } from "@/types/skill";

/**
 * v6.11.0 — resolve the tenant's default skill id for the skill dropdown.
 *
 * The default is stored tenant-side as `clients/me.default_skill` (a SLUG, e.g.
 * "one-assistant"); this hook fetches it once and maps it back to the concrete
 * skillId within the caller's `skills` list. The dropdown pins + highlights
 * that skill at the top. Friendly→id resolution (slug→skillId) happens here so
 * the presentation layer only ever deals in the canonical id (CLAUDE.md #9).
 *
 * Degrades to `null` on any failure — the dropdown then just skips the pinned
 * "Default" section (graceful, never blocks the picker).
 */
export function useTenantDefaultSkill(skills: Skill[]): string | null {
  const [defaultSlug, setDefaultSlug] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchWithAuth("/api/proxy/api/clients/me");
        if (!res.ok) return;
        const me = (await res.json()) as { default_skill?: string | null };
        if (!cancelled) setDefaultSlug(me.default_skill ?? null);
      } catch {
        // Non-fatal — no pinned default section.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!defaultSlug) return null;
  return skills.find((s) => s.slug && s.slug === defaultSlug)?.skillId ?? null;
}
