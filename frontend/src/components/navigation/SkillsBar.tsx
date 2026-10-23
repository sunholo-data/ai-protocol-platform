"use client";

import Link from "next/link";
import { Menu, PanelLeftClose, PanelLeftOpen, Plus, SquarePen } from "lucide-react";
import type { Skill } from "@/types/skill";
import { SettingsIcon } from "@/components/icons";
import { useSidebarToggle } from "@/components/shells/ShellActionsContext";
import { cn } from "@/lib/utils";
import { headerControl, headerIconControl } from "./headerControls";
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
  // Registered by the shell body that owns a sidebar (ChatShell) — absent in
  // shells without one, so the button only appears where it does something.
  const sidebar = useSidebarToggle();

  return (
    <header
      // Phones: no bar, no border — just menu · skill · new chat floating over
      // the page, the way native chat apps do it. From `sm` it is a full bar.
      className="flex h-14 shrink-0 items-center gap-2 bg-background px-2 sm:gap-3 sm:border-b sm:px-5 md:h-16 md:px-8"
      aria-label="Skills navigation"
    >
      {sidebar ? (
        <button
          type="button"
          onClick={sidebar.toggle}
          aria-expanded={sidebar.open}
          aria-label={sidebar.open ? "Hide conversations and files" : "Show conversations and files"}
          title={sidebar.open ? "Hide conversations and files" : "Show conversations and files"}
          className={cn(headerIconControl, "border-transparent text-muted-foreground hover:text-foreground")}
        >
          {/* Phones: a plain menu glyph — the sheet it opens holds history,
              files and the account. Desktop: the panel toggle it really is. */}
          <Menu className="h-5 w-5 sm:hidden" aria-hidden />
          {sidebar.open ? (
            <PanelLeftClose className="hidden h-5 w-5 sm:block" aria-hidden />
          ) : (
            <PanelLeftOpen className="hidden h-5 w-5 sm:block" aria-hidden />
          )}
        </button>
      ) : null}

      {/* Name-and-symbol lockup: the mark, then the product name in bold,
          vertically centred, at least a quarter of the mark's box apart
          (gap-3 = 12px against a 36px mark). The name drops below `sm` so the
          skill switcher keeps its room on a phone. */}
      {/* Below `sm` the lockup gives way to the skill name — on a phone the
          installed app's own icon already carries the brand. */}
      <Link
        href="/"
        className="hidden shrink-0 items-center gap-3 sm:flex rounded-[var(--radius)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        aria-label="Home"
      >
        {/* Brand mark uses chatAvatar — the tight, self-contained mark — so the
            top-left and every chat-bubble avatar are one consistent identity. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={BRANDING.logo.chatAvatar} alt={BRANDING.appName} className="h-9 w-9 rounded-full" />
        <span className="hidden text-lg font-bold text-foreground sm:inline md:text-xl">{BRANDING.appName}</span>
      </Link>

      <div className="flex min-w-0 flex-1 items-center gap-2 max-sm:justify-center sm:flex-initial" data-testid="skill-switcher-region">
        {isLoading ? (
          <SkillSwitcherSkeleton />
        ) : skills.length === 0 ? (
          <span className="text-sm text-muted-foreground">No skills yet — create your first one →</span>
        ) : (
          <SkillSwitcher
            skills={skills}
            activeSkillId={activeSkillId}
            defaultSkillId={defaultSkillId}
            currentUserId={currentUserId}
          />
        )}
        {hasActiveSkill ? (
          <button
            type="button"
            onClick={onNewConversation}
            title="Start a new conversation"
            aria-label="Start a new conversation"
            className={cn(headerControl, "hidden w-11 px-0 sm:inline-flex md:w-auto md:px-3")}
          >
            <SquarePen className="h-4 w-4" aria-hidden />
            <span className="hidden md:inline">New chat</span>
          </button>
        ) : null}
      </div>

      {/* Phones: new chat is a plain icon at the far right. */}
      {hasActiveSkill ? (
        <button
          type="button"
          onClick={onNewConversation}
          title="Start a new conversation"
          aria-label="Start a new conversation"
          className={cn(headerIconControl, "border-transparent text-muted-foreground hover:text-foreground sm:hidden")}
        >
          <SquarePen className="h-5 w-5" aria-hidden />
        </button>
      ) : null}

      <div className="hidden flex-1 sm:flex" />

      <div className="flex shrink-0 items-center gap-2">
        {hasActiveSkill ? (
          <button
            type="button"
            onClick={onConfigureClick}
            title="Configure this skill"
            aria-label="Configure this skill"
            className={cn(headerIconControl, "hidden sm:inline-flex")}
          >
            <SettingsIcon className="h-4 w-4" />
          </button>
        ) : null}

        <button
          type="button"
          onClick={onCreateClick}
          title="Create a new skill"
          aria-label="Create a new skill"
          className={cn(headerIconControl, "hidden sm:inline-flex")}
        >
          <Plus className="h-4 w-4" aria-hidden />
        </button>

        {/* Phones: when the body has a menu sheet, the account lives at its foot
            (AccountPanel) and the avatar leaves the header. Otherwise the
            avatar menu stays, carrying Configure + Create on phones. */}
        <div className={sidebar ? "hidden sm:block" : undefined}>
        <UserMenu
          phoneItems={[
            ...(hasActiveSkill
              ? [{ label: "Configure this skill", icon: <SettingsIcon className="h-4 w-4" />, onSelect: onConfigureClick }]
              : []),
            { label: "Create a new skill", icon: <Plus className="h-4 w-4" aria-hidden />, onSelect: onCreateClick },
          ]}
        />
        </div>
      </div>
    </header>
  );
}

function SkillSwitcherSkeleton() {
  return (
    <div className="flex items-center gap-2" data-testid="skill-tabs-skeleton">
      <div className="h-11 w-40 animate-pulse rounded-[var(--radius)] bg-muted md:h-9" />
    </div>
  );
}
