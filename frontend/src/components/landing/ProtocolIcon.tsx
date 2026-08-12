/**
 * Pillar icons for the landing "Built on" stripe.
 *
 * Two pillar sets are supported in tandem so a deployment can flip
 * between them by editing `branding.ts`:
 *
 *   • Capability pillars (default Aitana/Sunholo) — what the platform
 *     does in business terms: extract / compare / benchmark / compliance
 *     / citation / confidential. These ship as the new defaults
 *     (v6.4.0 INTERNAL-SHELL iteration 2026-06-09).
 *
 *   • Protocol pillars — what the platform is built on for technical
 *     audiences: ag-ui / a2ui / mcp / mcp-apps / a2a / adk. Kept so
 *     forks targeting developer audiences can repopulate
 *     `BRANDING.demo.pillars` with the protocol keys.
 *
 * Filename stays `ProtocolIcon` for import-path stability; consider
 * renaming to `PillarIcon` in a follow-up if no forks are pinned to
 * the import path.
 */
export type ProtocolIconKey =
  // Business capabilities (default)
  | "extract"
  | "compare"
  | "benchmark"
  | "compliance"
  | "citation"
  | "confidential"
  // Technical protocols (opt-in for developer-audience forks)
  | "ag-ui"
  | "a2ui"
  | "mcp"
  | "mcp-apps"
  | "a2a"
  | "adk";

interface ProtocolIconProps {
  pillar: ProtocolIconKey;
  className?: string;
}

export function ProtocolIcon({
  pillar,
  className = "h-5 w-5",
}: ProtocolIconProps) {
  const sharedProps = {
    className,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "1.6",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": "true" as const,
  };
  switch (pillar) {
    // ── Business capabilities ──────────────────────────────────────────

    case "extract":
      // Document with magnifier — extract structured data from a document.
      return (
        <svg {...sharedProps}>
          <path d="M5 3h9l4 4v10H5z" />
          <path d="M14 3v4h4" />
          <circle cx="10" cy="13" r="2" />
          <path d="M11.5 14.5L13 16" />
        </svg>
      );
    case "compare":
      // Two facing panels — side-by-side comparison.
      return (
        <svg {...sharedProps}>
          <rect x="3" y="4" width="8" height="16" rx="1" />
          <rect x="13" y="4" width="8" height="16" rx="1" />
          <path d="M5 8h4M5 12h4M15 8h4M15 14h4" />
        </svg>
      );
    case "benchmark":
      // Bar chart — market-price benchmarking.
      return (
        <svg {...sharedProps}>
          <path d="M3 20h18" />
          <rect x="5" y="13" width="3" height="7" />
          <rect x="10.5" y="9" width="3" height="11" />
          <rect x="16" y="6" width="3" height="14" />
        </svg>
      );
    case "compliance":
      // Shield with checkmark — regulatory compliance check.
      return (
        <svg {...sharedProps}>
          <path d="M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6l8-3z" />
          <path d="M9 12l2.5 2.5L15 11" />
        </svg>
      );
    case "citation":
      // Linked chain — every value linked to its source.
      return (
        <svg {...sharedProps}>
          <path d="M10 14a4 4 0 005.66 0l2.83-2.83a4 4 0 00-5.66-5.66L11 7" />
          <path d="M14 10a4 4 0 00-5.66 0L5.51 12.83a4 4 0 005.66 5.66L13 17" />
        </svg>
      );
    case "confidential":
      // Lock — data stays in your cloud.
      return (
        <svg {...sharedProps}>
          <rect x="5" y="11" width="14" height="9" rx="1.5" />
          <path d="M8 11V7a4 4 0 018 0v4" />
        </svg>
      );

    // ── Technical protocols (opt-in for developer-audience forks) ──────

    case "ag-ui":
      return (
        <svg {...sharedProps}>
          <path d="M2 12c2 0 2-4 4-4s2 8 4 8 2-6 4-6 2 4 4 4 2-2 4-2" />
        </svg>
      );
    case "a2ui":
      return (
        <svg {...sharedProps}>
          <rect x="3" y="4" width="18" height="16" rx="1.5" />
          <path d="M3 8h18M7 12h10M7 16h6" />
        </svg>
      );
    case "mcp":
      return (
        <svg {...sharedProps}>
          <path d="M14.5 9.5L20 4l-2 6-4 4-2-2 2.5-2.5z" />
          <path d="M14 14L4 4M10 16l-6 6 2-4 4-2z" />
        </svg>
      );
    case "mcp-apps":
      return (
        <svg {...sharedProps}>
          <path d="M9 3v4M15 3v4" />
          <rect x="6" y="7" width="12" height="6" rx="1.5" />
          <path d="M12 13v4M8 21h8M10 17h4" />
        </svg>
      );
    case "a2a":
      return (
        <svg {...sharedProps}>
          <path d="M3 8h14l-3-3M21 16H7l3 3" />
        </svg>
      );
    case "adk":
      return (
        <svg {...sharedProps}>
          <circle cx="12" cy="4.5" r="1.8" />
          <circle cx="4.5" cy="12" r="1.8" />
          <circle cx="19.5" cy="12" r="1.8" />
          <circle cx="12" cy="19.5" r="1.8" />
          <path d="M12 6.3v3.9M12 13.8v3.9M6.3 12h3.9M13.8 12h3.9" />
        </svg>
      );
  }
}
