"use client";

import { Volume2, VolumeX } from "lucide-react";
import { useAutoReadAloud } from "@/hooks/useAutoReadAloud";

export function AutoReadToggle() {
  const { enabled, toggle } = useAutoReadAloud();
  return (
    <button type="button" onClick={toggle} aria-pressed={enabled}
      aria-label="Automatically read replies aloud" title="Automatically read replies aloud"
      className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground">
      {enabled ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
      <span>{enabled ? "Auto-read on" : "Auto-read off"}</span>
    </button>
  );
}
