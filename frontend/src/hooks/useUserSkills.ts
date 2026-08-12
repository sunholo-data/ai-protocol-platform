"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import type { Skill } from "@/types/skill";

interface UseUserSkillsReturn {
  skills: Skill[];
  isLoading: boolean;
  error: string | null;
}

// Sentinel ownerId for skills shipped by Aitana Labs (the five defaults
// available to every tenant). Mirrored from backend/skills/platform.py.
const PLATFORM_OWNER_UID = "aitana-platform";

/**
 * Returns the skills shown in the SkillsBar: the user's own skills plus the
 * platform-global defaults. Platform skills come last and are deduped against
 * the user's own list so a fork keeps the user's version (different skillId,
 * same display name is fine — both render).
 */
export function useUserSkills(uid: string | null): UseUserSkillsReturn {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!uid) {
      setSkills([]);
      setError(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsLoading(true);
    setError(null);

    const own = fetchWithAuth(
      `/api/proxy/api/skills?ownerId=${encodeURIComponent(uid)}`,
      { signal: controller.signal },
    ).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<Skill[]>;
    });

    const platform = fetchWithAuth(
      `/api/proxy/api/skills?ownerId=${encodeURIComponent(PLATFORM_OWNER_UID)}`,
      { signal: controller.signal },
    ).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json() as Promise<Skill[]>;
    });

    Promise.all([own, platform])
      .then(([ownSkills, platformSkills]) => {
        const seen = new Set(ownSkills.map((s) => s.skillId));
        const merged = [...ownSkills, ...platformSkills.filter((s) => !seen.has(s.skillId))];
        setSkills(merged);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setError("Could not load skills.");
          setSkills([]);
        }
      })
      .finally(() => setIsLoading(false));

    return () => abortRef.current?.abort();
  }, [uid]);

  return { skills, isLoading, error };
}
