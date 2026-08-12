"use client";

import { useRef } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { DocTab, type DocTabData } from "./DocTab";

interface DocTabsBarProps {
  tabs: DocTabData[];
  activeTabId: string | null;
  showBrowser: boolean;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onToggleInclude: (id: string) => void;
  onToggleBrowser: () => void;
}

export function DocTabsBar({
  tabs,
  activeTabId,
  showBrowser,
  onSelect,
  onClose,
  onToggleInclude,
  onToggleBrowser,
}: DocTabsBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  return (
    <div className="flex items-stretch border-b bg-muted/30">
      {/* Browser toggle */}
      <button
        type="button"
        onClick={onToggleBrowser}
        className={[
          "flex shrink-0 items-center border-r px-2.5 text-muted-foreground hover:bg-accent hover:text-foreground",
          showBrowser ? "bg-accent/50 text-foreground" : "",
        ].join(" ")}
        title={showBrowser ? "Hide sidebar" : "Show sidebar"}
        aria-label={showBrowser ? "Hide sidebar" : "Show sidebar"}
      >
        {showBrowser ? (
          <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
        ) : (
          <PanelLeftOpen className="h-4 w-4" aria-hidden="true" />
        )}
      </button>

      {/* Tab strip — horizontally scrollable, no scrollbar */}
      <div
        ref={scrollRef}
        role="tablist"
        className="flex min-w-0 flex-1 overflow-x-auto"
        style={{ scrollbarWidth: "none" }}
      >
        {tabs.length === 0 && (
          <span className="flex items-center px-3 text-xs text-muted-foreground">
            No open documents
          </span>
        )}
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
