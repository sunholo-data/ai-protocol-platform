"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { EmptyTab } from "./EmptyTab";

/**
 * Default 4-breakpoint width scale (v6.4.0 INTERNAL-SHELL M2).
 *
 * Tight on laptops (520px), comfortable on 1080p (640px), generous on
 * 1440p (760px), expansive on ultrawide (860px). Used when no `className`
 * is passed; explicit `className` always wins.
 *
 * Lifted from gde-ap-agent chat-page line 419 verbatim.
 */
const DEFAULT_WIDTH_SCALE =
  "md:w-[520px] xl:w-[640px] 2xl:w-[760px] [@media(min-width:2000px)]:w-[860px]";

/**
 * Workbench — persistent tabbed pane that replaces the single-slot
 * conditional ladder used by chat pages with multiple right-pane
 * surfaces (Document ⊕ Workspace ⊕ MCP App embeds ⊕ Analytics).
 *
 * G31 (template-chat-surface-defaults.md): the prior pattern
 *
 *   {expandedTab && <DocumentPanel/>}
 *   {!expandedTab && !globeContext && <WorkspaceSurfaceRegion/>}
 *   {!expandedTab && globeContext && !dashboardOpen && <VendorGlobePanel/>}
 *
 * swapped the slot wholesale on every surface_action. MCP App iframes
 * remounted on every switch (~200ms postMessage re-handshake flash) and
 * users lost mental-model context ("where did my invoice go?").
 *
 * Workbench fixes this by keeping all tabs mounted and using `hidden` to
 * toggle visibility. MCP App iframes preserve their handshake state
 * across tab switches; the parent badges inactive tabs when new content
 * arrives instead of swapping panes.
 *
 * Tab badges: pass `badged: true` to indicate "new content arrived while
 * this tab was inactive." Clears the moment the tab is activated. The
 * parent owns badging state — see `useTabBadges()` below for a typed
 * helper.
 *
 * Ported from gde-ap-agent fork 2026-06-05; template-agnostic.
 */

export interface WorkbenchTab {
  /** Stable id matching what `activeTabId` references. */
  id: string;
  /** Short label shown on the tab itself. */
  label: string;
  /** Optional eyebrow rendered above the label (e.g. "MCP App"). */
  eyebrow?: string;
  /** Optional hover tooltip (native title) — e.g. an artifact's full filename +
   * details when the visible label is a short tool/kind name (7.5). */
  tooltip?: string;
  /** When true a small primary-dot appears, meaning "new content here". */
  badged?: boolean;
  /** Optional disabled state — render greyed out, unclickable. */
  disabled?: boolean;
  /**
   * When set, the tab is CLOSABLE: an `×` renders after the label and calls
   * this. Omit for STRUCTURAL tabs (Workspace/Home, Document, Activity) — they
   * are the pane's furniture, not results, and closing them is meaningless.
   *
   * The Workbench does NOT confirm — it has no idea what closing costs. The
   * owner of the tab decides (and, for a result that cost a BigQuery job or a
   * paid analysis, MUST confirm before dropping it). See ChatShell's
   * `handleCloseArtifact`.
   */
  onClose?: () => void;
  /** The tab body. Always rendered; visibility-toggled by `hidden` class
   * so iframe handshakes and other expensive mount state persist. May be
   * null when the tab has no content — pair with `emptyBody` to render a
   * contextual EmptyTab instead of a blank panel (v6.4.0 INTERNAL-SHELL M2). */
  content: React.ReactNode | null;
  /** Optional empty-state body shown when `content` is null. Title is
   * derived from `label`. When omitted and `content` is null, the tab
   * body renders nothing. */
  emptyBody?: string;
}

interface WorkbenchProps {
  tabs: WorkbenchTab[];
  /** Controlled active-tab id. Pass-through to parent so external events
   * (e.g. agent emitting a surface_action) can set the active tab. */
  activeTabId: string;
  onActiveTabChange: (id: string) => void;
  className?: string;
}

export function Workbench({
  tabs,
  activeTabId,
  onActiveTabChange,
  className,
}: WorkbenchProps) {
  const tabListRef = useRef<HTMLDivElement | null>(null);

  // Keyboard navigation (Left/Right) — accessible-by-default tab strip.
  useEffect(() => {
    const el = tabListRef.current;
    if (!el) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const idx = tabs.findIndex((t) => t.id === activeTabId);
      if (idx === -1) return;
      const dir = e.key === "ArrowRight" ? 1 : -1;
      // Skip disabled tabs.
      for (let i = 1; i <= tabs.length; i++) {
        const next = tabs[(idx + dir * i + tabs.length) % tabs.length];
        if (!next.disabled) {
          onActiveTabChange(next.id);
          return;
        }
      }
    }
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, [tabs, activeTabId, onActiveTabChange]);

  return (
    <div
      className={cn(
        "flex shrink-0 flex-col overflow-hidden border-l border-border bg-background",
        // Width scale: explicit className wins; otherwise apply 4-breakpoint default.
        className ?? DEFAULT_WIDTH_SCALE,
      )}
    >
      <header className="flex items-stretch border-b border-border bg-muted/10">
        <div className="flex items-center gap-3 border-r border-border px-4">
          <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
            Workbench
          </span>
        </div>
        {/* Tab strip scrolls horizontally when it overflows, but the native
            scrollbar is hidden (`no-scrollbar`) — it was big + ugly with many
            tabs. Labels truncate (full text in the `title` tooltip), and a soft
            right-edge fade hints there's more to scroll. */}
        <div
          ref={tabListRef}
          role="tablist"
          aria-label="Workbench tabs"
          className="no-scrollbar flex flex-1 overflow-x-auto"
        >
          {tabs.map((tab) => {
            const isActive = tab.id === activeTabId;
            const closable = Boolean(tab.onClose) && !tab.disabled;
            // The `×` is a real <button>, so it cannot live INSIDE the tab
            // button (nested interactive content is invalid HTML and steals
            // the click). Tab + `×` are siblings in a `group` wrapper that
            // carries the active underline across both.
            return (
              <div
                key={tab.id}
                className={cn(
                  "group relative flex shrink-0 items-stretch transition-colors",
                  isActive ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                  tab.disabled && "opacity-40",
                )}
              >
                <button
                  role="tab"
                  aria-selected={isActive}
                  aria-controls={`workbench-panel-${tab.id}`}
                  tabIndex={isActive ? 0 : -1}
                  disabled={tab.disabled}
                  title={tab.tooltip}
                  onClick={() => onActiveTabChange(tab.id)}
                  className={cn(
                    "flex max-w-[13rem] shrink-0 items-baseline gap-2 py-3 pl-4 text-left",
                    closable ? "pr-1" : "pr-4",
                    tab.disabled && "cursor-not-allowed",
                  )}
                >
                  {tab.eyebrow && (
                    <span className="shrink-0 font-mono text-[9px] uppercase tracking-wider text-muted-foreground/70">
                      {tab.eyebrow}
                    </span>
                  )}
                  <span className="truncate text-sm font-semibold tracking-tight">{tab.label}</span>
                  {tab.badged && !isActive && (
                    <span
                      aria-label="new content"
                      className="relative ml-0.5 flex h-1.5 w-1.5 shrink-0 items-center justify-center"
                    >
                      {/* Soft ping halo — three pulses then naturally fades;
                          works in tandem with the solid dot so the eye is
                          drawn to the tab. (v6.4.0 INTERNAL-SHELL M2) */}
                      <span className="absolute inline-flex h-2.5 w-2.5 animate-ping rounded-full bg-primary/40" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
                    </span>
                  )}
                </button>
                {closable && (
                  <button
                    type="button"
                    // Named by the FRIENDLY tab label, never a surfaceId
                    // (CLAUDE.md #9) — this string is what a screen-reader
                    // user hears before they destroy a result.
                    aria-label={`Close ${tab.label}`}
                    title={`Close ${tab.label}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      tab.onClose?.();
                    }}
                    className={cn(
                      "mr-2 flex shrink-0 items-center self-center rounded p-1 transition-opacity",
                      "hover:bg-muted hover:text-foreground",
                      "focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      // Stays out of the way until the tab is hovered/focused
                      // or active — a row of always-on `×`s reads as clutter.
                      "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
                      isActive && "opacity-60 hover:opacity-100",
                    )}
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                )}
                {isActive && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2 -bottom-px h-0.5 origin-center animate-in fade-in zoom-in-x-50 rounded-t-sm bg-primary duration-200"
                  />
                )}
              </div>
            );
          })}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          // Empty-state rendering when content is null and emptyBody is set
          // (v6.4.0 INTERNAL-SHELL M2). Otherwise render content as-is.
          const tabBody =
            tab.content == null && tab.emptyBody
              ? <EmptyTab title={tab.label} body={tab.emptyBody} />
              : tab.content;
          return (
            <div
              key={tab.id}
              role="tabpanel"
              id={`workbench-panel-${tab.id}`}
              aria-hidden={!isActive}
              className={cn(
                "h-full w-full overflow-auto",
                // Gentle fade on activation. tailwindcss-animate's
                // `animate-in` only fires when the element first appears;
                // toggling between hidden/visible re-runs it each switch
                // (v6.4.0 INTERNAL-SHELL M2).
                isActive ? "animate-in fade-in duration-200" : "hidden",
              )}
            >
              {tabBody}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Convenience hook: tracks which inactive tabs have "received content
 * since they were last seen" so the parent can pass `badged` flags.
 *
 * Usage:
 *   const { mark, isBadged, clearOnActivate } = useTabBadges();
 *   useEffect(() => { if (newPayloadArrived) mark("workspace"); }, [...]);
 *   <Workbench
 *     tabs={[{ id: "workspace", badged: isBadged("workspace"), ... }]}
 *     activeTabId={current}
 *     onActiveTabChange={(id) => { clearOnActivate(id); setCurrent(id); }}
 *   />
 */
export function useTabBadges() {
  const [badged, setBadged] = useState<Record<string, boolean>>({});
  return {
    mark: (id: string) => setBadged((p) => ({ ...p, [id]: true })),
    clear: (id: string) => setBadged((p) => ({ ...p, [id]: false })),
    clearOnActivate: (id: string) =>
      setBadged((p) => (p[id] ? { ...p, [id]: false } : p)),
    isBadged: (id: string) => Boolean(badged[id]),
  };
}
