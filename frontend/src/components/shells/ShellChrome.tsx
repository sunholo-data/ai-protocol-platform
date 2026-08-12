"use client";

import { type ReactNode } from "react";
import { useRouter } from "next/navigation";
import type { User } from "@/lib/firebase";
import { useUserSkills } from "@/hooks/useUserSkills";
import { useTenantDefaultSkill } from "@/hooks/useTenantDefaultSkill";
import { SkillsBar } from "@/components/navigation/SkillsBar";
import { skillHref } from "@/components/navigation/skillHref";

export interface ShellChromeProps {
  skillId: string;
  user: User;
  children: ReactNode;
}

/**
 * v6.6.0 — global chrome shared by every shell mode.
 *
 * The SkillsBar (Home link + skill switcher) used to live inside ChatShell
 * only, so a user routed into a specialised shell (doc-compare /
 * workbench-primary) — e.g. by the auth-landing redirect resuming their last
 * session — landed with no header and no way to switch skills or get home.
 *
 * Lifting it here makes the top nav universal: ShellRouter wraps every mode in
 * ShellChrome, so the specialised shells keep their intentional body (full-
 * viewport workspace + chat drawer) but never strand the user. The shell body
 * fills the remaining height below the bar.
 */
export function ShellChrome({ skillId, user, children }: ShellChromeProps) {
  const { skills, isLoading } = useUserSkills(user.uid);
  const defaultSkillId = useTenantDefaultSkill(skills);
  const defaultSkill = skills.find((s) => s.skillId === defaultSkillId);
  const router = useRouter();

  return (
    // `h-full`, not `h-screen` (v6.19.0, AIPLA #23). Claiming 100vh here is
    // wrong whenever anything else occupies vertical space in the root layout —
    // a banner above us is additive, and the bottom of this shell (the chat
    // input) ends up below the fold. `h-full` fills whatever the layout's
    // `flex-1 min-h-0` wrapper actually grants us; `min-h-0` lets us shrink
    // below content height so the inner scroll regions do the scrolling.
    <div className="flex h-full min-h-0 flex-col">
      <SkillsBar
        skills={skills}
        activeSkillId={skillId}
        isLoading={isLoading}
        defaultSkillId={defaultSkillId}
        currentUserId={user.uid}
        onCreateClick={() => router.push("/skills/studio/new")}
        onConfigureClick={() => router.push(`/skills/studio/${skillId}`)}
        // New chat opens a FRESH session, NEVER resuming. Route straight to a
        // skill with NO ?session= so useStableThreadId mints a new thread
        // (X → null → fresh). Prefer the tenant DEFAULT skill (the front door —
        // ONE Assistant); if it isn't configured/resolved yet, fall back to a
        // fresh session of the CURRENT skill (always known), then the first
        // enabled skill — anything but "/", which useLandingTarget would RESUME
        // the most-recent session on (the bug: a tenant with no default_skill
        // had New chat bounce back to the last thread). 2026-07-16.
        onNewConversation={() => {
          const target = defaultSkill ?? skills.find((s) => s.skillId === skillId) ?? skills[0];
          router.push(target ? skillHref(target) : "/");
        }}
      />
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
