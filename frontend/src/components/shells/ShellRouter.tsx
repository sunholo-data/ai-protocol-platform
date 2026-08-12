"use client";

import type { User } from "@/lib/firebase";
import type { SkillShell } from "@/types/skill";
import { ChatShell } from "@/components/chat/ChatShell";
import { DocCompareShell } from "@/components/shells/DocCompareShell";
import { WorkbenchShell } from "@/components/shells/WorkbenchShell";
import { ShellChrome } from "@/components/shells/ShellChrome";

export interface ShellRouterProps {
  skillId: string;
  pathPrefix: string;
  user: User;
  /** Resolved by useSlugResolution from the same by-slug fetch — no extra
   * round-trip, so the mode is known before the first shell paint (no
   * mount-then-swap flash). Null → chat-primary. */
  shell: SkillShell | null;
}

/**
 * v6.4.0 SHELL-MODES — page-level shell dispatch.
 *
 * Reads the skill's declared `shell.mode` and renders the matching page-level
 * shell. `chat-primary` (the default), `custom`, unknown modes, and a null
 * shell all fall back to ChatShell — graceful degradation so a skill written
 * against a newer schema never renders a blank page on an older client.
 *
 * Every mode is wrapped in ShellChrome (v6.6.0) so the global top nav (Home +
 * skill switcher) is universal — a user routed into a specialised shell is
 * never stranded without a way to navigate.
 *
 * See docs/design/v6.4.0/skill-driven-shell-modes.md.
 */
export function ShellRouter({ shell, ...props }: ShellRouterProps) {
  const mode = shell?.mode ?? "chat-primary";
  const body = (() => {
    switch (mode) {
      case "doc-compare":
        return <DocCompareShell shell={shell} {...props} />;
      case "workbench-primary":
        return <WorkbenchShell shell={shell} {...props} />;
      case "custom":
      case "chat-primary":
      default:
        return <ChatShell {...props} />;
    }
  })();

  return (
    <ShellChrome skillId={props.skillId} user={props.user}>
      {body}
    </ShellChrome>
  );
}
