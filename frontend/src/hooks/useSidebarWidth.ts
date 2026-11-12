"use client";

/**
 * Width of the chat page's left sidebar (files and conversations), dragged by
 * SidebarResizeHandle and remembered in this browser. Desktop only — below
 * `md` the sidebar is a full-width sheet and ignores it.
 */

import { useCallback, useEffect, useState } from "react";

export const SIDEBAR_DEFAULT = 256;
export const SIDEBAR_MIN = 224;
export const SIDEBAR_MAX = 560;
const KEY = "aiplatform.sidebarWidth";

export function clampSidebarWidth(px: number): number {
  if (!Number.isFinite(px)) return SIDEBAR_DEFAULT;
  return Math.round(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, px)));
}

function readStored(): number | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    const n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) ? clampSidebarWidth(n) : null;
  } catch {
    return null;
  }
}

function writeStored(px: number): void {
  try {
    window.localStorage.setItem(KEY, String(px));
  } catch {
    // Private mode / blocked storage: the width just isn't remembered.
  }
}

export function useSidebarWidth(): { width: number; setWidth: (px: number) => void } {
  const [width, setWidthState] = useState(SIDEBAR_DEFAULT);
  useEffect(() => {
    const stored = readStored();
    if (stored !== null) setWidthState(stored);
  }, []);
  const setWidth = useCallback((px: number) => {
    const next = clampSidebarWidth(px);
    setWidthState(next);
    writeStored(next);
  }, []);
  return { width, setWidth };
}
