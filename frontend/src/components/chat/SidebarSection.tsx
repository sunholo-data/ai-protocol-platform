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
    <details open={open} className="group border-b border-border">
      <summary
        onClick={(e) => {
          // Drive open state ourselves so it can be persisted; preventDefault
          // stops the native toggle from also firing (which would double-toggle).
          e.preventDefault();
          toggle();
        }}
        className="flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground/60 hover:text-muted-foreground"
      >
        <SectionChevron />
        <span className="flex-1 truncate">{title}</span>
        {badge}
        {action}
      </summary>
      <div className={bodyClassName ?? "px-3 pb-3 pt-1"}>{children}</div>
    </details>
  );
}

function SectionChevron() {
  return (
    <svg
      className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90"
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
