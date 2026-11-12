"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { Skill } from "@/types/skill";
import { PLATFORM_OWNER_UID } from "@/lib/platformOwner";
import { sharedRequest } from "@/lib/sharedRequest";

interface UseUserSkillsReturn {
  skills: Skill[];
  isLoading: boolean;
  error: string | null;
}

/**
 * Returns the skills shown in the SkillsBar: the user's own skills plus the
 * platform-global defaults. Platform skills come last and are deduped against
 * the user's own list so a fork keeps the user's version (different skillId,
 * same display name is fine — both render).
 */
/** Merging the burst of mounts on one page load — not a cache the user could
 * notice after editing a skill. */
const SKILLS_SHARE_MS = 5_000;

// Keyed by the VIEWER as well as the owner: the platform list is filtered by
// the caller's access, so one person's list must never reach the next person
// to sign in on the same tab inside the window.
function loadSkills(viewerUid: string, ownerId: string): Promise<Skill[]> {
  return sharedRequest(
    `skills:${viewerUid}:${ownerId}`,
    () =>
      fetchWithAuth(`/api/proxy/api/skills?ownerId=${encodeURIComponent(ownerId)}`).then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<Skill[]>;
      }),
    SKILLS_SHARE_MS,
  );
}

export function useUserSkills(uid: string | null): UseUserSkillsReturn {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!uid) {
      setSkills([]);
      setError(null);
      return;
    }
    // Shared with every other reader on the page, so never aborted: a uid
    // change (or unmount) just ignores this request's result.
    let current = true;
    setIsLoading(true);
    setError(null);

    Promise.all([loadSkills(uid, uid), loadSkills(uid, PLATFORM_OWNER_UID)])
      .then(([ownSkills, platformSkills]) => {
        if (!current) return;
        const seen = new Set(ownSkills.map((s) => s.skillId));
        const merged = [...ownSkills, ...platformSkills.filter((s) => !seen.has(s.skillId))];
        setSkills(merged);
      })
      .catch(() => {
        if (!current) return;
        setError("Could not load skills.");
        setSkills([]);
      })
      .finally(() => {
        if (current) setIsLoading(false);
      });

    return () => {
      current = false;
    };
  }, [uid]);

  return { skills, isLoading, error };
}
