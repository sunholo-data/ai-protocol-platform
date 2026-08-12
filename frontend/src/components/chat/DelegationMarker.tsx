// SKILL-DELEGATION M3 — a persistent, low-key inline marker in the transcript
// recording a skill→skill handoff. Part of the activity-transparency surface:
// it "lights up" when it appears, then reads as quiet background context that
// stays available in history. Presentational only.

"use client";

interface DelegationMarkerProps {
  targetDisplay: string;
  /** "auto" = a completed handoff; "suggest" = a proposed handoff awaiting confirm. */
  mode: "auto" | "suggest";
}

export function DelegationMarker({ targetDisplay, mode }: DelegationMarkerProps) {
  const label =
    mode === "suggest" ? (
      <>
        Suggested <span className="font-medium text-foreground/70">{targetDisplay}</span>
      </>
    ) : (
      <>
        Delegated to <span className="font-medium text-foreground/70">{targetDisplay}</span>
      </>
    );

  return (
    <div
      className="flex items-center gap-2 py-0.5 text-xs text-muted-foreground"
      role="note"
      aria-label={mode === "suggest" ? `Suggested handoff to ${targetDisplay}` : `Delegated to ${targetDisplay}`}
    >
      <span className="ml-10 flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5">
        {/* arrow-right (SVG, no emoji) */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="text-orange-500/70"
        >
          <path d="M5 12h14" />
          <path d="m13 5 7 7-7 7" />
        </svg>
        <span>{label}</span>
      </span>
    </div>
  );
}
