"use client";

/**
 * "Your instructions" — the default project's instructions (projects design
 * §6.5): one per-user text the agent gets in every chat.
 *
 * Shared store, because two surfaces read it at once: the card that edits it
 * (new-chat landing, settings) and the chat banner that says "Your
 * instructions on". A save in the card updates the banner without a refetch.
 */

import { useCallback, useSyncExternalStore } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { subscribeToAuthState } from "@/lib/firebase";

const API = "/api/proxy/api/me/instructions";

/** Server-side cap (§5, `users/{uid}.instructions`). */
export const MAX_INSTRUCTIONS_CHARS = 4000;

export type InstructionsStatus = "loading" | "ready" | "error";

interface State {
  instructions: string;
  status: InstructionsStatus;
}

let state: State = { instructions: "", status: "loading" };
let loaded = false;
let inFlight: Promise<void> | null = null;
const listeners = new Set<() => void>();

function emit(next: State) {
  state = next;
  for (const l of listeners) l();
}

function load(): Promise<void> {
  if (inFlight) return inFlight;
  if (!loaded && state.status !== "loading") emit({ ...state, status: "loading" });
  inFlight = (async () => {
    try {
      const res = await fetchWithAuth(API);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = (await res.json()) as { instructions?: unknown };
      loaded = true;
      emit({ instructions: typeof body?.instructions === "string" ? body.instructions : "", status: "ready" });
    } catch {
      if (!loaded) emit({ instructions: "", status: "error" });
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}


// The store outlives a sign-out: without this, the next person to sign in on
// the same tab would briefly see the previous user's instructions until a refetch.
let authUid: string | null | undefined;
let authUnsub: (() => void) | null = null;
function watchAuth() {
  if (authUnsub) return;
  authUnsub = subscribeToAuthState((user) => {
    const uid = user?.uid ?? null;
    if (authUid !== undefined && uid !== authUid) {
      loaded = false;
      inFlight = null;
      emit({ instructions: "", status: "loading" });
      if (uid && listeners.size > 0) void load();
    }
    authUid = uid;
  });
}

function subscribe(listener: () => void): () => void {
  watchAuth();
  listeners.add(listener);
  if (!loaded) void load();
  return () => listeners.delete(listener);
}

const getSnapshot = () => state;

/** Test-only: a cold store per test. */
export function __resetMyInstructionsStore(): void {
  authUnsub?.();
  authUnsub = null;
  authUid = undefined;
  state = { instructions: "", status: "loading" };
  loaded = false;
  inFlight = null;
  listeners.clear();
}

export type SaveResult = { ok: true } | { ok: false; message: string };

/** PUT the new text; on success every reader sees it at once. */
export async function saveMyInstructions(instructions: string): Promise<SaveResult> {
  const fallback = "Couldn't save your instructions";
  let res: Response;
  try {
    res = await fetchWithAuth(API, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instructions }),
    });
  } catch {
    return { ok: false, message: `${fallback} (network error).` };
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
    const d = body?.detail;
    const message =
      typeof d === "string" && d
        ? d
        : d && typeof d === "object" && !Array.isArray(d) && typeof (d as { message?: unknown }).message === "string"
          ? String((d as { message: string }).message)
          : res.status === 422
            ? `Your instructions can be at most ${MAX_INSTRUCTIONS_CHARS.toLocaleString()} characters.`
            : `${fallback} (HTTP ${res.status}).`;
    return { ok: false, message };
  }
  const body = (await res.json().catch(() => null)) as { instructions?: unknown } | null;
  loaded = true;
  emit({ instructions: typeof body?.instructions === "string" ? body.instructions : instructions, status: "ready" });
  return { ok: true };
}

export function useMyInstructions(): { instructions: string; status: InstructionsStatus; retry: () => void } {
  const snap = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const retry = useCallback(() => {
    loaded = false;
    void load();
  }, []);
  return { instructions: snap.instructions, status: snap.status, retry };
}
