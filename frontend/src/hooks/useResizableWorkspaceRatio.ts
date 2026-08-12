"use client";

import { useCallback, useEffect, useState } from "react";

/** Resize allowed range. 0.30 = chat takes 70%; 1.00 = chat hidden. */
export const RATIO_MIN = 0.3;
export const RATIO_MAX = 1.0;

/** Per-skill default workspace ratio. Skills not listed fall through to
 * RATIO_DEFAULT. The chat-page's auto-fold logic hides the workbench
 * column entirely when there's no content (no workspace surface, no
 * open doc tab, no fresh-chat picker), so this ratio only kicks in
 * once the workbench actually has something to show.
 *
 * Hardcoded for now — the `SkillConfig.uiHints.defaultWorkspaceRatio`
 * schema field is a future addition. */
export const DEFAULT_RATIOS: Record<string, number> = {
  "one-doc-compare": 0.6, // workbench centerpiece; gets more room
};

/** Half-and-half default when content arrives. Skills that want more or
 * less workspace room override via DEFAULT_RATIOS. */
export const RATIO_DEFAULT = 0.5;

const COLLAPSED_KEY_PREFIX = "aitana.workspaceCollapsed:";

/** Read the per-skill user-collapsed flag from sessionStorage. SSR-safe.
 *
 * This is DISTINCT from the chat-page-level auto-fold (which hides the
 * workbench when there's truly nothing to show — no surface + no tab +
 * no examples). The collapsed flag is an explicit user gesture:
 * "I want this hidden even when there IS content, until I expand it
 * again." Persisted per-skillId so toggling on one skill doesn't fold
 * the other.
 */
export function readStoredCollapsed(skillId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(COLLAPSED_KEY_PREFIX + skillId) === "1";
  } catch {
    return false;
  }
}

export function writeStoredCollapsed(skillId: string, collapsed: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(COLLAPSED_KEY_PREFIX + skillId, collapsed ? "1" : "0");
  } catch {
    // Quota / disabled storage — silently ignore.
  }
}

const STORAGE_PREFIX = "aitana.workspaceRatio:";

function storageKey(skillId: string): string {
  return STORAGE_PREFIX + skillId;
}

function clampRatio(v: number): number {
  if (!Number.isFinite(v)) return RATIO_DEFAULT;
  return Math.min(RATIO_MAX, Math.max(RATIO_MIN, v));
}

/** Read a stored ratio for a skill. Returns null when nothing stored or
 * the stored value is malformed / out of range. SSR-safe. */
export function readStoredRatio(skillId: string): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(storageKey(skillId));
    if (raw === null) return null;
    const parsed = parseFloat(raw);
    if (!Number.isFinite(parsed)) return null;
    if (parsed < RATIO_MIN || parsed > RATIO_MAX) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeStoredRatio(skillId: string, ratio: number): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(storageKey(skillId), String(ratio));
  } catch {
    // Quota / disabled storage — silently ignore. Resize still works in
    // memory; just doesn't survive the tab.
  }
}

function defaultRatioFor(skillId: string): number {
  return DEFAULT_RATIOS[skillId] ?? RATIO_DEFAULT;
}

export interface UseResizableWorkspaceRatio {
  /** Workspace fraction (0..1) — the right pane's share of the row. */
  ratio: number;
  /** Update + persist. Clamped to [RATIO_MIN, RATIO_MAX] internally. */
  setRatio: (next: number) => void;
}

/**
 * Lifted state for the chat ↔ workspace split ratio. Returns the current
 * ratio (initialised from sessionStorage with a per-skill default
 * fallback) plus a setter that persists on call.
 *
 * Ported from CPH UNI's AIPLA fork 2026-06-11. Storage prefix changed
 * from `aipla.workspaceRatio:` to `aitana.workspaceRatio:` so the two
 * apps don't clobber each other if a developer flips between them on
 * the same origin.
 *
 * SSR note: initial state is the per-skill default to avoid SSR
 * divergence (no sessionStorage on the server). The stored ratio loads
 * in a useEffect on mount — there's a one-frame visual flash on hydrate
 * which is acceptable for a workspace layout.
 */
export function useResizableWorkspaceRatio(skillId: string): UseResizableWorkspaceRatio {
  const [ratio, setRatioInternal] = useState<number>(() => defaultRatioFor(skillId));

  useEffect(() => {
    const stored = readStoredRatio(skillId);
    setRatioInternal(stored ?? defaultRatioFor(skillId));
  }, [skillId]);

  const setRatio = useCallback(
    (next: number) => {
      const clamped = clampRatio(next);
      setRatioInternal(clamped);
      writeStoredRatio(skillId, clamped);
    },
    [skillId],
  );

  return { ratio, setRatio };
}
