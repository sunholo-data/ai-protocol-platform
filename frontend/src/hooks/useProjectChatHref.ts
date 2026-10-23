"use client";

import { useCallback } from "react";
import { skillHref } from "@/components/navigation/skillHref";
import { useTenantDefaultSkill } from "@/hooks/useTenantDefaultSkill";
import { useUserSkills } from "@/hooks/useUserSkills";
import type { Skill } from "@/types/skill";

/** The chat URL that opens a new chat scoped to a project, optionally asking
 * `text` straight away (ChatShell consumes `?project=` and `?ask=`). */
export function projectChatHref(base: string, projectId: string, text = ""): string {
  const qs = new URLSearchParams({ project: projectId });
  if (text.trim()) qs.set("ask", text.trim());
  return `${base}${base.includes("?") ? "&" : "?"}${qs.toString()}`;
}

/** The chat URL for a skill id, friendly when we know the skill. */
export function chatBase(skills: Skill[], skillId: string | null): string | null {
  if (!skillId) return null;
  const skill = skills.find((s) => s.skillId === skillId);
  return skill ? skillHref(skill) : `/chat/${encodeURIComponent(skillId)}`;
}

/**
 * Where "chat in this project" goes, for any project: the normal chat screen,
 * scoped to the project (`?project=`), on the project's own default skill, else
 * the tenant default, else the user's first skill.
 *
 * One rule everywhere a project is opened (sidebar, projects list, settings),
 * so the same project always opens on the same assistant. Returns null until a
 * skill is known; callers fall back (the sidebar to the chat it is in).
 */
export function useProjectChatHref(
  uid: string | null,
): (project: { id: string; defaultSkillId?: string | null }, ask?: string) => string | null {
  const { skills } = useUserSkills(uid);
  const tenantDefault = useTenantDefaultSkill(skills);
  return useCallback(
    (project, ask = "") => {
      const skillId = project.defaultSkillId ?? tenantDefault ?? skills[0]?.skillId ?? null;
      const base = chatBase(skills, skillId);
      return base ? projectChatHref(base, project.id, ask) : null;
    },
    [skills, tenantDefault],
  );
}
