// Chat-placement A2UI surfaces — rendered INLINE in the chat thread (not a
// workbench tab). The obligation ELICITATION form (7.8 M1): a
// `map_ppa_obligations` refusal on a template contract becomes a multi-field
// A2UI form (DateTimeInput + numeric TextField per placeholder + "Run the
// analysis" button — backend result→A2UI mapping, Model B) instead of a
// dead-end prose refusal.
//
// Protocols-first (CLAUDE.md "user interaction that feeds data back to the AI
// renders as A2UI in the chat area via the surface-action loop"): the form is a
// backend-emitted A2UI surface rendered by the GENERIC `A2UISurfaceMount` with
// `triggerOnAction`, so its submit button drives a full `surface-action-run`
// turn. The filled field values ride the surface data model
// (`readA2uiSurfaceState` snapshot → `forwardedProps.a2ui_surface_state`) into
// the re-run, where `map_ppa_obligations` reads them AUTHORITATIVELY (no LLM
// transcription of the trust-critical numbers) and completes the analysis.
//
// Discovery is generic: any artifact whose backend `artifact.placement` is
// `"chat"` renders here; every other artifact stays a workbench tab. So a
// second tool adopting the elicitation envelope gets a chat form for free.
//
// PLACEMENT (7.8): these surfaces are INTERLEAVED into the message timeline by
// creation time — `ChatMessageList` renders each `ChatPlacementForm` at its
// chronological position (a message sent AFTER a form appears below it, a
// re-refusal's next form appears below the message before it). See
// `ChatMessageList`'s `chatSurfaces` prop.

"use client";

import { A2UISurfaceMount } from "@/components/protocols/A2UISurfaceMount";
import { useArtifacts } from "@/providers/SurfaceRegistry";

/** Artifact kind for the obligation elicitation FORM (interactive; freezes to a
 * static "Submitted" record once superseded). Other chat-placement kinds (e.g.
 * a result summary) are static by nature and never freeze. */
export const ELICITATION_FORM_KIND = "obligation-elicitation-form";

export interface ChatPlacementFormProps {
  surfaceId: string;
  /** True once a newer surface exists after this FORM — render it read-only as
   * a static submission record (the entered values stay visible). */
  submitted: boolean;
  /** Current chat session id — required for the surface-action-run POST. */
  sessionId: string | null;
  /** Skill id — surface-action-run is scoped to the skill. */
  skillId: string;
  /** A plain confirm (no fields) vs a field form. Both render at the same width
   * now; the flag only selects the `--confirm` skin modifier (auto-width,
   * right-aligned CTA). */
  isConfirm?: boolean;
  /** Header label for the card, from the surface's artifact metadata (e.g.
   * "Confirm" / "Provide details"). Backend-provided so it stays a friendly,
   * AI-authored label rather than a hardcoded string. */
  title?: string;
}

/**
 * ONE chat-placement A2UI surface as a conversation card. A titled header frames
 * the ask; the body is the backend-emitted A2UI (description + fields + CTA). A
 * submitted form is frozen read-only with a "Submitted" header (append-only
 * history); an active form renders interactive.
 */
export function ChatPlacementForm({ surfaceId, submitted, sessionId, skillId, isConfirm, title }: ChatPlacementFormProps) {
  // Header label: the backend artifact title when active; a clear "Submitted"
  // once frozen. Fallback keeps a header present even if metadata is missing.
  const headerLabel = submitted ? "Submitted" : title || (isConfirm ? "Confirm" : "Action needed");
  return (
    // A conversation card that reads as part of the thread (left-aligned like
    // assistant content). Widened so the header + description + CTA have room to
    // breathe (previously a plain confirm was capped at max-w-md — reversed per
    // 2026-07-16 user feedback that the cards felt cramped). `chat-a2ui-form`
    // carries the theme-aware A2UI skin (globals.css) that tames the SDK's raw
    // inline styles; a plain confirm still gets the `--confirm` modifier
    // (auto-width, right-aligned CTA).
    <div
      className={
        "w-full max-w-2xl " +
        "overflow-hidden rounded-xl border shadow-sm " +
        // Active card: a clean, slightly-raised surface with a subtle primary
        // left-accent so it reads as "an action is waiting for you". Submitted:
        // a flat dashed record.
        (submitted
          ? "border-dashed bg-muted/10"
          : "border-l-2 border-l-primary/60 bg-card")
      }
      data-chat-form={surfaceId}
      data-submitted={submitted || undefined}
    >
      {/* Header bar — always present. Active: the ask's title. Submitted: a
          "✓ Submitted" record marker. */}
      <div
        className={
          "flex items-center gap-1.5 border-b px-4 py-2 " +
          (submitted
            ? "bg-muted/40 text-xs font-medium text-muted-foreground"
            : "text-sm font-semibold text-foreground")
        }
      >
        {submitted && (
          <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M20 6 9 17l-5-5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
        {headerLabel}
      </div>
      {/* A submitted form is a static record: no interaction, entered values
          remain visible. `pointer-events-none` freezes it; not passing
          `triggerOnAction` also prevents any run. */}
      <div className={submitted ? "pointer-events-none opacity-75" : undefined}>
        <A2UISurfaceMount
          surfaceId={surfaceId}
          sessionId={sessionId}
          skillId={skillId}
          triggerOnAction={!submitted}
          className={"chat-a2ui-form px-4 py-3" + (isConfirm ? " chat-a2ui-form--confirm" : "")}
        />
      </div>
    </div>
  );
}

/** One chat-placement surface with the metadata `ChatMessageList` needs to
 * interleave it into the transcript by time. */
export interface ChatSurfaceItem {
  surfaceId: string;
  /** Registry emit time (ms) — sorted against message `createdAt`. */
  createdAt: number;
  /** True once superseded (frozen "Submitted" record). */
  submitted: boolean;
  /** Plain confirm (no fields) → gets the `--confirm` skin modifier. */
  isConfirm: boolean;
  /** Header label from the artifact metadata (e.g. "Confirm"/"Provide details"). */
  title?: string;
  /** Restored from history (not a live turn) → anchor strictly by `createdAt`. */
  replayed: boolean;
}

/**
 * Build the time-ordered chat-surface list for `ChatMessageList` from the
 * SurfaceRegistry. MUST be called inside `SurfaceRegistryProvider`. A FORM is
 * `submitted` (frozen) once it is not the last chat surface; result summaries
 * are not forms, so they never freeze.
 */
export function useChatSurfaces(): ChatSurfaceItem[] {
  const chat = useArtifacts().filter((a) => a.placement === "chat");
  const lastId = chat[chat.length - 1]?.surfaceId;
  return chat.map((a) => ({
    surfaceId: a.surfaceId,
    createdAt: a.createdAt,
    // Frozen (a static submitted record) when: replayed from history — the action
    // already happened / the session moved on (v6.10.0); OR an obligation form
    // superseded by a newer surface.
    submitted: Boolean(a.replayed) || (a.kind === ELICITATION_FORM_KIND && a.surfaceId !== lastId),
    isConfirm: a.elicitationKind === "confirm",
    title: a.title,
    replayed: Boolean(a.replayed),
  }));
}

export interface ChatPlacementFormsProps {
  sessionId: string | null;
  skillId: string;
}

/**
 * All chat-placement surfaces in creation order (non-interleaved). Retained for
 * standalone use/tests; the live transcript interleaves them by time via
 * `ChatMessageList`'s `chatSurfaces` prop instead of rendering this block.
 */
export function ChatPlacementForms({ sessionId, skillId }: ChatPlacementFormsProps) {
  const surfaces = useChatSurfaces();
  if (surfaces.length === 0) return null;
  return (
    <div className="space-y-3" data-testid="chat-placement-forms">
      {surfaces.map((s) => (
        <ChatPlacementForm
          key={s.surfaceId}
          surfaceId={s.surfaceId}
          submitted={s.submitted}
          sessionId={sessionId}
          skillId={skillId}
          isConfirm={s.isConfirm}
          title={s.title}
        />
      ))}
    </div>
  );
}
