"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { skillHref } from "@/components/navigation/skillHref";
import type { Skill } from "@/types/skill";

/**
 * Where to send a signed-in user who lands on `/` (v6.5.0 AUTH-LANDING):
 *   - resume : their most-recent still-openable chat
 *   - fresh  : a new chat with the deployment's primary skill
 *   - landing: nothing to route to → show the marketplace/landing
 */
export type LandingTarget =
  | { kind: "loading" }
  | { kind: "resume"; href: string }
  | { kind: "fresh"; href: string }
  | { kind: "landing" };

interface RecentSession {
  session_id: string;
  skill_id: string;
  slug: string | null;
  owner_id: string;
}

/**
 * Resolve the authenticated landing target.
 *
 * 1. `GET /api/sessions/recent` → resume the last openable chat.
 * 2. else pick the primary skill: the enabled skill matching `clients/me`'s
 *    `default_skill`, falling back to the first enabled skill.
 * 3. else `landing` (no skills / unconfigured) — the homepage stays put.
 *
 * Only fires when `enabled` (caller passes `!authLoading && !!user`). Any
 * failure degrades to `landing` so a backend hiccup never traps the user.
 */
export function useLandingTarget(enabled: boolean): LandingTarget {
  const [target, setTarget] = useState<LandingTarget>({ kind: "loading" });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    (async () => {
      try {
        const recent = await fetchWithAuth("/api/proxy/api/sessions/recent");
        if (recent.status === 200) {
          const s = (await recent.json()) as RecentSession;
          const base = skillHref({ skillId: s.skill_id, slug: s.slug, ownerId: s.owner_id });
          if (!cancelled) {
            setTarget({ kind: "resume", href: `${base}?session=${encodeURIComponent(s.session_id)}` });
          }
          return;
        }
        // 204 (or anything non-200) → fall through to the primary skill.
        const [meRes, skillsRes] = await Promise.all([
          fetchWithAuth("/api/proxy/api/clients/me"),
          fetchWithAuth("/api/proxy/api/skills"),
        ]);
        const me = meRes.ok ? await meRes.json() : {};
        const skills = (skillsRes.ok ? await skillsRes.json() : []) as Skill[];
        const primary = skills.find((k) => k.slug && k.slug === me.default_skill) ?? skills[0];
        if (cancelled) return;
        setTarget(
          primary?.slug && primary?.ownerId
            ? { kind: "fresh", href: skillHref(primary) }
            : { kind: "landing" },
        );
      } catch {
        if (!cancelled) setTarget({ kind: "landing" });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return target;
}
