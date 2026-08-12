// Confirm→SWITCH (8.2, "full switch" semantics — 2026-07-15).
//
// When the user confirms a handoff, the route does what a manual skill-menu pick
// does, but on their behalf: it SWITCHES the session to the target specialist —
// same thread (context carries natively via `use_thread_id_as_session_id`), so
// documents + history come along — and the specialist answers the outstanding
// request and STAYS active for the rest of the session.
//
// Two channels:
//  1. An in-page bus (A2UISurfaceMount → the front-door ChatShell). The confirm
//     card lives inside A2UISurfaceMount, which knows the target skill id but not
//     the user's outstanding request; ChatShell owns the transcript, so it reads
//     the exact prompt and drives the navigation.
//  2. A sessionStorage stash that survives the navigation (front-door ChatShell →
//     the specialist's ChatShell), carrying the prompt + document ids so the
//     specialist re-issues the request through the NORMAL chat path (no bespoke
//     inline-render machinery — the reply streams like any other turn).

export interface SkillSwitchIntent {
  /** Canonical target skill id (from the confirm card's `context.target_skill_id`). */
  targetSkillId: string;
}

type Listener = (intent: SkillSwitchIntent) => void;
const listeners = new Set<Listener>();

/** A2UISurfaceMount fires this when the user Proceeds on a `confirm_delegation` card. */
export function emitSkillSwitchIntent(intent: SkillSwitchIntent): void {
  for (const listener of listeners) listener(intent);
}

/** The front-door ChatShell subscribes to turn the intent into a stash + navigation. */
export function subscribeSkillSwitchIntent(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Handoff carried across the navigation to the specialist's ChatShell. */
export interface PendingSkillSwitch {
  /** Thread the switch happened on — must match the specialist's session for it
   * to be a true continuation (same ADK session → context carries). */
  threadId: string;
  /** Canonical target skill id — must match the specialist ChatShell's skillId. */
  targetSkillId: string;
  /** The user's outstanding request, re-issued on the specialist. */
  prompt: string;
  /** Document ids in context on the front door, carried so the specialist's first
   * turn processes the same documents. */
  documentIds: string[];
}

const STASH_KEY = "aitana:pending-skill-switch";

export function stashPendingSkillSwitch(pending: PendingSkillSwitch): void {
  try {
    sessionStorage.setItem(STASH_KEY, JSON.stringify(pending));
  } catch {
    // sessionStorage unavailable (private mode / SSR) — the switch still
    // navigates; the specialist just won't auto-continue. Non-fatal.
  }
}

export function readPendingSkillSwitch(): PendingSkillSwitch | null {
  try {
    const raw = sessionStorage.getItem(STASH_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<PendingSkillSwitch>;
    if (
      v &&
      typeof v.threadId === "string" &&
      typeof v.targetSkillId === "string" &&
      typeof v.prompt === "string" &&
      Array.isArray(v.documentIds)
    ) {
      return {
        threadId: v.threadId,
        targetSkillId: v.targetSkillId,
        prompt: v.prompt,
        documentIds: v.documentIds.filter((d): d is string => typeof d === "string"),
      };
    }
  } catch {
    // Corrupt stash — drop it.
  }
  return null;
}

export function clearPendingSkillSwitch(): void {
  try {
    sessionStorage.removeItem(STASH_KEY);
  } catch {
    // ignore
  }
}
