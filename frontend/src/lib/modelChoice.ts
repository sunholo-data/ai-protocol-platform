"use client";

/**
 * The composer's Model menu (docs/design/v6.42.0/model-choice.md).
 *
 * Three levels: everyone gets Auto (the skill's own model); anyone can pick
 * Fast or Thorough from the +; people who turn on "Choose a specific model" on
 * the Settings page also see the models this deployment allows. The backend
 * (GET /api/models/choice) says what is offered here, already filtered for
 * residency and mounted provider keys; this module decides what to show for
 * the skill in front of the user.
 */
import { useEffect, useState, useSyncExternalStore } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

export const AUTO = "auto";
export type Level = "fast" | "thorough";

export interface ModelMenu {
  offered: "off" | "tiers" | "all";
  levels: Partial<Record<Level, { model: string; label: string }>>;
  models: { id: string; label: string; note?: string; provider?: string }[];
  /** Friendly name for any tier or registry id. */
  labels: Record<string, string>;
  /** Registry id each tier or id runs as here. */
  resolved: Record<string, string>;
}

export interface SkillModelInfo {
  name: string;
  /** The skill's own `model` (tier or registry id). */
  model: string;
  thinking: string;
  /** False: the skill keeps its own model whatever the person picks. */
  modelChoice: boolean;
  /** A front door: its delegates follow the pick even if it doesn't. */
  canDelegate: boolean;
}

export interface MenuOption {
  value: string;
  title: string;
  detail: string;
}

export interface ModelOptions {
  /** Show the Model row at all. */
  show: boolean;
  /** "X always uses Y" when the skill keeps its model and hands nothing on. */
  locked: string | null;
  levels: MenuOption[];
  models: MenuOption[];
}

const HIDDEN: ModelOptions = { show: false, locked: null, levels: [], models: [] };

function labelOf(menu: ModelMenu, ref: string): string {
  return menu.labels[ref] ?? ref;
}

/**
 * What the Model menu shows for one skill. A level that would run exactly like
 * Auto for a skill that answers itself is hidden rather than offered as a
 * choice that changes nothing (e.g. Thorough on an EU-only deployment, where
 * the smart and pro tiers are one model and high thinking measured the same as
 * dynamic).
 */
export function buildModelOptions(
  menu: ModelMenu | null,
  skill: SkillModelInfo | null,
  specificOptIn: boolean,
): ModelOptions {
  if (!menu || !skill || !skill.model || menu.offered === "off") return HIDDEN;
  if (!skill.modelChoice && !skill.canDelegate) {
    return { ...HIDDEN, show: true, locked: `${skill.name} always uses ${labelOf(menu, skill.model)}.` };
  }
  const answersItself = !skill.canDelegate;
  const own = menu.resolved[skill.model] ?? skill.model;
  const autoDetail = answersItself
    ? `${skill.name} uses ${labelOf(menu, skill.model)}`
    : "Each assistant uses the model it's set up with";
  const levels: MenuOption[] = [{ value: AUTO, title: "Auto", detail: autoDetail }];
  const fast = menu.levels.fast;
  if (fast && !(answersItself && fast.model === own)) {
    levels.push({ value: "fast", title: "Fast", detail: `${fast.label} · quicker, for simple questions` });
  }
  const thorough = menu.levels.thorough;
  if (thorough && !(answersItself && thorough.model === own && skill.thinking === "dynamic")) {
    levels.push({ value: "thorough", title: "Thorough", detail: `${thorough.label} · slower, for hard problems` });
  }
  const models =
    menu.offered === "all" && specificOptIn
      ? menu.models.map((m) => ({ value: `model:${m.id}`, title: m.label, detail: m.note ?? "" }))
      : [];
  // Nothing to choose between: no row at all.
  if (levels.length === 1 && models.length === 0) return HIDDEN;
  return { show: true, locked: null, levels, models };
}

/** The chip's text for a pick, or null for Auto. */
export function choiceLabel(menu: ModelMenu | null, choice: string | undefined): string | null {
  if (!choice || choice === AUTO) return null;
  if (choice === "fast") return "Fast";
  if (choice === "thorough") return "Thorough";
  if (choice.startsWith("model:")) {
    const id = choice.slice("model:".length);
    return menu?.labels[id] ?? id;
  }
  return null;
}

// --- GET /api/models/choice, fetched once per page load ----------------------

let menuPromise: Promise<ModelMenu | null> | null = null;

function loadMenu(): Promise<ModelMenu | null> {
  menuPromise ??= fetchWithAuth("/api/proxy/api/models/choice")
    .then((res) => (res.ok ? (res.json() as Promise<ModelMenu>) : null))
    .catch(() => null)
    .then((menu) => {
      // A failed fetch is retried on the next mount instead of sticking.
      if (!menu) menuPromise = null;
      return menu;
    });
  return menuPromise;
}

/** Test seam. */
export function resetModelMenuCache() {
  menuPromise = null;
}

export function useModelMenu(): ModelMenu | null {
  const [menu, setMenu] = useState<ModelMenu | null>(null);
  useEffect(() => {
    let cancelled = false;
    void loadMenu().then((m) => {
      if (!cancelled) setMenu(m);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return menu;
}

// --- Settings → Models → "Choose a specific model" (per browser) -------------

const OPT_IN_KEY = "platform.models.specific";
const OPT_IN_EVENT = "platform:models.preference";

function readOptIn(): boolean {
  try {
    return window.localStorage.getItem(OPT_IN_KEY) === "1";
  } catch {
    return false;
  }
}

function subscribe(listener: () => void) {
  window.addEventListener(OPT_IN_EVENT, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(OPT_IN_EVENT, listener);
    window.removeEventListener("storage", listener);
  };
}

export function setSpecificModelsOptIn(on: boolean) {
  try {
    window.localStorage.setItem(OPT_IN_KEY, on ? "1" : "0");
  } catch {
    /* storage unavailable: the switch just won't stick */
  }
  window.dispatchEvent(new Event(OPT_IN_EVENT));
}

export function useSpecificModelsOptIn(): boolean {
  return useSyncExternalStore(subscribe, readOptIn, () => false);
}
