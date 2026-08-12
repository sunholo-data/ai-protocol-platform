// MODEL-RELIABILITY M3 — a persistent, low-key inline notice in the transcript
// recording that a backup model answered (or that a benched provider was
// skipped). Degradation is announced, never hidden: the user deserves to know
// which model produced the answer they're reading (axioms #2 EARNED TRUST +
// #5 GRACEFUL DEGRADATION). Mirrors DelegationMarker. Presentational only.

"use client";

/** "anthropic/claude-opus-4-7" → "claude-opus-4-7" — drop provider prefixes
 *  for display; the full id stays in the tooltip. */
function displayName(model: string): string {
  return model.includes("/") ? model.split("/").pop()! : model;
}

interface FallbackNoticeProps {
  fromModel: string;
  toModel: string;
  /** "provider_cooldown" = primary skipped (known-bad), not tried-and-failed. */
  reason?: string;
}

export function FallbackNotice({ fromModel, toModel, reason }: FallbackNoticeProps) {
  const verb = reason === "provider_cooldown" ? "unavailable — answered by backup" : "was unavailable — answered by backup";

  return (
    <div
      className="flex items-center gap-2 py-0.5 text-xs text-muted-foreground"
      role="note"
      aria-label={`Backup model ${displayName(toModel)} answered because ${displayName(fromModel)} was unavailable`}
      title={`${fromModel} → ${toModel}${reason ? ` (${reason})` : ""}`}
    >
      <span className="ml-10 flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5">
        {/* shuffle/switch (SVG, no emoji) */}
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
          className="text-amber-500/70"
        >
          <path d="m18 14 4 4-4 4" />
          <path d="m18 2 4 4-4 4" />
          <path d="M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-8.6a4 4 0 0 1 3.3-1.7H22" />
          <path d="M2 6h1.972a4 4 0 0 1 3.6 2.2" />
          <path d="M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45" />
        </svg>
        <span>
          <span className="font-medium text-foreground/70">{displayName(fromModel)}</span> {verb}{" "}
          <span className="font-medium text-foreground/70">{displayName(toModel)}</span>
        </span>
      </span>
    </div>
  );
}
