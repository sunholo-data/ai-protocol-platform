"use client";

import { useEffect, useState, type ReactNode } from "react";

interface SidebarSectionProps {
  title: string;
  defaultOpen?: boolean;
  /**
   * Stable key. When set, the section's open/closed state is remembered in
   * localStorage across sidebar reopens and page loads (workspace-wide, not
   * per-skill). Omit for ephemeral sections.
   */
  persistId?: string;
  badge?: ReactNode;
  action?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
}

const STORAGE_PREFIX = "aitana.sidebar.section.";

/**
 * Uniform collapsible sidebar section (v6.4.0 INTERNAL-SHELL M1; persistence
 * v6.6.0).
 *
 * Headers are always visible so the user can reach every section regardless of
 * which others are expanded; each body is constrained so a single long section
 * can't push others off-screen.
 *
 * Controlled `<details>` — keeps native a11y (screen-reader announces
 * expand/collapse, keyboard Enter/Space toggles) while letting us remember the
 * open state. Without control, the sidebar remounting (toggle shut → reopen)
 * reset every section back to its default.
 */
export function SidebarSection({
  title,
  defaultOpen = true,
  persistId,
  badge,
  action,
  bodyClassName,
  children,
}: SidebarSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  // Hydrate the remembered state after mount (avoids an SSR/client mismatch).
  // localStorage can throw (Safari private mode / storage disabled) — never let
  // that crash the sidebar; just fall back to the default.
  useEffect(() => {
    if (!persistId) return;
    try {
      const stored = window.localStorage.getItem(STORAGE_PREFIX + persistId);
      if (stored === "1" || stored === "0") setOpen(stored === "1");
    } catch {
      /* storage unavailable — keep defaultOpen */
    }
  }, [persistId]);

  function toggle() {
    setOpen((prev) => {
      const next = !prev;
      if (persistId) {
        try {
          window.localStorage.setItem(STORAGE_PREFIX + persistId, next ? "1" : "0");
        } catch {
          /* storage unavailable — state still toggles for this session */
        }
      }
      return next;
    });
  }

  return (
    <details open={open} className="group/section pt-2">
      <summary
        onClick={(e) => {
          // Drive open state ourselves so it can be persisted; preventDefault
          // stops the native toggle from also firing (which would double-toggle).
          e.preventDefault();
          toggle();
        }}
        // Sentence case, readable weight (brand: no all-caps headings); a
        // 44px row on phones so the sheet's sections are easy to tap.
        className="flex min-h-[44px] cursor-pointer select-none items-center gap-1.5 px-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground md:min-h-[28px] md:px-3"
      >
        <span className="flex-1 truncate">{title}</span>
        {badge}
        {action}
        <SectionChevron />
      </summary>
      <div className={bodyClassName ?? "px-2 pb-2"}>{children}</div>
    </details>
  );
}

function SectionChevron() {
  return (
    <svg
      className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-[transform,opacity] group-open/section:rotate-90 group-hover/section:opacity-100 [@media(hover:none)]:opacity-100"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}
