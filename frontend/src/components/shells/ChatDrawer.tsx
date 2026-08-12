"use client";

import { useEffect, useState, type ReactNode } from "react";

export interface ChatDrawerProps {
  /** Which edge the drawer is anchored to. DocCompareShell uses "right",
   * WorkbenchShell uses "left". */
  side: "left" | "right";
  /** Initial open state. "open" expands to full width; "minimised" (default)
   * collapses to a handle the user clicks to expand. */
  defaultState?: "open" | "minimised" | "hidden";
  /** Accessible label + collapsed-handle caption. */
  label?: string;
  /** Expanded width in px. Default 380. */
  width?: number;
  children: ReactNode;
}

/**
 * v6.4.0 SHELL-MODES — slide-out chat drawer shared by the non-chat-primary
 * shells. Chat is secondary in those shells, so it lives in a collapsible
 * drawer instead of a full column.
 *
 * The children stay MOUNTED when collapsed (hidden via CSS) so the live
 * agent stream / message state survives a collapse-expand toggle.
 */
export function ChatDrawer({
  side,
  defaultState = "minimised",
  label = "Chat",
  width = 380,
  children,
}: ChatDrawerProps) {
  const [open, setOpen] = useState(defaultState === "open");

  // ESC collapses an open drawer (matches the rest of the app's overlay UX).
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const borderClass = side === "right" ? "border-l" : "border-r";

  return (
    <div
      data-testid="chat-drawer"
      data-side={side}
      data-open={open ? "true" : "false"}
      className={`flex h-full shrink-0 flex-col bg-background transition-[width] duration-200 ${borderClass}`}
      style={{ width: open ? width : 44 }}
    >
      <button
        type="button"
        aria-label={open ? `Collapse ${label}` : `Expand ${label}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 border-b px-3 py-2 text-left text-sm hover:bg-muted/50"
      >
        <span aria-hidden="true" className="text-muted-foreground">
          {open === (side === "right") ? "›" : "‹"}
        </span>
        {open && <span className="font-medium">{label}</span>}
      </button>
      {/* Kept mounted when collapsed so the agent stream persists. */}
      <div className={open ? "min-h-0 flex-1 overflow-hidden" : "hidden"}>{children}</div>
    </div>
  );
}
