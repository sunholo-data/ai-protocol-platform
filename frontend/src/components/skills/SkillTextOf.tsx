"use client";

import { useSkillText, type SkillTextSource } from "@/hooks/useSkillText";

/**
 * One of a skill's reader-facing fields, in the person's language — for server
 * components (the landing page's skill list) that can't call `useSkillText`.
 * The server renders the deployment default; a person who chose another
 * language sees theirs once the page hydrates.
 */
export function SkillTextOf({
  skill,
  field,
  fallback = "",
}: {
  skill: SkillTextSource;
  field: "displayName" | "description";
  /** Shown when the skill has no text for the field (e.g. its internal name). */
  fallback?: string;
}) {
  return <>{useSkillText(skill)[field] || fallback}</>;
}
