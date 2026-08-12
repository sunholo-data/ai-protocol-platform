"use client";

import Link from "next/link";
import { Plus, SquarePen } from "lucide-react";
import type { Skill } from "@/types/skill";
import { SettingsIcon } from "@/components/icons";
import { SkillSwitcher } from "./SkillSwitcher";
import { UserMenu } from "./UserMenu";
import { BRANDING } from "@/lib/branding";

interface SkillsBarProps {
  skills: Skill[];
  activeSkillId: string;
  isLoading: boolean;
  /** Tenant default skill id — pinned + highlighted in the picker. */
  defaultSkillId?: string | null;
  /** Signed-in user's uid — their own skills group under "Your skills". */
  currentUserId?: string | null;
  onCreateClick: () => void;
  onConfigureClick: () => void;
  /** Start a fresh conversation with the active skill (drops ?session=). */
  onNewConversation: () => void;
}

export function SkillsBar({
  skills,
  activeSkillId,
  isLoading,
  defaultSkillId,
  currentUserId,
  onCreateClick,
  onConfigureClick,
  onNewConversation,
}: SkillsBarProps) {
  const hasActiveSkill = skills.some((s) => s.skillId === activeSkillId);

  return (
    <header
      className="flex h-12 items-center gap-2 border-b bg-background px-3"
      aria-label="Skills navigation"
    >
      <Link href="/" className="flex shrink-0 items-center" aria-label="Home">
        {/* Brand mark uses chatAvatar — the tight, self-contained animated mark
            that fills its circle — so the top-left and every chat-bubble avatar
            are ONE consistent, legible identity. The full animated hero
            (heroAnimated, with the wordmark) stays on the landing screen where
            it has room; at 28px its big internal margins rendered a tiny smudge. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={BRANDING.logo.chatAvatar}
          alt={BRANDING.appName}
          className="h-8 w-8 rounded-full"
        />
      </Link>

      <div className="flex min-w-0 items-center gap-2" data-testid="skill-switcher-region">
        {isLoading ? (
          <SkillSwitcherSkeleton />
        ) : skills.length === 0 ? (
          <span className="text-xs text-muted-foreground">No skills yet — create your first one →</span>
        ) : (
          <SkillSwitcher
            skills={skills}
            activeSkillId={activeSkillId}
            defaultSkillId={defaultSkillId}
            currentUserId={currentUserId}
          />
        )}
      </div>

      {hasActiveSkill ? (
        <button
          type="button"
          onClick={onNewConversation}
          title="Start a new conversation"
          aria-label="Start a new conversation"
          className="flex h-8 shrink-0 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <SquarePen className="h-4 w-4" aria-hidden />
          <span className="hidden sm:inline">New chat</span>
        </button>
      ) : null}

      <div className="flex flex-1" />

      {hasActiveSkill ? (
        <button
          type="button"
          onClick={onConfigureClick}
          title="Configure this skill"
          aria-label="Configure this skill"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <SettingsIcon className="h-4 w-4" />
        </button>
      ) : null}

      <button
        type="button"
        onClick={onCreateClick}
        title="Create a new skill"
        aria-label="Create a new skill"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <Plus className="h-4 w-4" aria-hidden />
      </button>

      <UserMenu />
    </header>
  );
}

function SkillSwitcherSkeleton() {
  return (
    <div className="flex items-center gap-2" data-testid="skill-tabs-skeleton">
      <div className="h-8 w-40 animate-pulse rounded-md bg-muted" />
    </div>
  );
}
