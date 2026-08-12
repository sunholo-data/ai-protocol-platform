import type { Skill } from "@/types/skill";

/**
 * Pick the best chat URL for a skill.
 *
 * Friendly URL `/chat/@{ownerId}/{slug}` when slug is present (handled by
 * the catch-all route in app/chat/[...path]/page.tsx). Falls back to the
 * UUID route `/chat/{skillId}` so links still work for skills that haven't
 * had a slug assigned yet.
 */
export function skillHref(skill: Pick<Skill, "skillId" | "slug" | "ownerId">): string {
  if (skill.slug && skill.ownerId) {
    return `/chat/@${encodeURIComponent(skill.ownerId)}/${encodeURIComponent(skill.slug)}`;
  }
  return `/chat/${encodeURIComponent(skill.skillId)}`;
}
