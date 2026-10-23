"use client";

import { useRef } from "react";
import { DocTab, type DocTabData } from "./DocTab";

interface DocTabsBarProps {
  tabs: DocTabData[];
  activeTabId: string | null;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onToggleInclude: (id: string) => void;
  /** @deprecated The sidebar toggle moved to the header (SkillsBar, via
   * ShellActionsContext). Accepted and ignored so existing call sites compile. */
  showBrowser?: boolean;
  /** @deprecated See `showBrowser`. */
  onToggleBrowser?: () => void;
}

/**
 * The open-document tab strip. Renders nothing when no document is open — an
 * empty strip saying so cost a row of a phone screen to state the obvious.
 */
export function DocTabsBar({ tabs, activeTabId, onSelect, onClose, onToggleInclude }: DocTabsBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  if (tabs.length === 0) return null;

  return (
    <div className="flex items-stretch border-b border-border bg-background">
      {/* Tab strip — horizontally scrollable, no scrollbar */}
      <div
        ref={scrollRef}
        role="tablist"
        className="flex min-w-0 flex-1 overflow-x-auto"
        style={{ scrollbarWidth: "none" }}
      >
        {tabs.map((tab) => (
          <DocTab
            key={tab.id}
            tab={tab}
            isActive={tab.id === activeTabId}
            onSelect={onSelect}
            onClose={onClose}
            onToggleInclude={onToggleInclude}
          />
        ))}
      </div>
    </div>
  );
}
