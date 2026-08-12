"use client";

import { cn } from "@/lib/utils";

/**
 * SkillStatusBadge — surfaces categorical status tags declared on a skill's
 * SKILL.md frontmatter (`tags:` list): `experimental`, `dev-tool`, `a2ui-demo`,
 * `system`.
 *
 * Two variants:
 *   - `badge` (default) — a full pill with a text label. Used where there is
 *     vertical room and the label is informative (the home-page skill list).
 *   - `dot` — a single small coloured dot with the label in a tooltip /
 *     aria-label. Used in the dense SkillsBar, where text pills crowd the tab
 *     row and force skill-name truncation. Keeps the day-to-day bar minimal
 *     and professional; the status is still a glance away on hover.
 *
 * In a customer deployment the enabled-skills filter hides demo/experimental
 * skills, so the bar shows no dots at all — this indicator only appears for
 * admins looking at WIP skills in local dev / the marketplace.
 *
 * Unknown tags are silently ignored — anything not in KNOWN_TAGS is skipped
 * rather than rendered as a noisy default. Adding a new status means adding a
 * variant here too; that keeps the palette under design control.
 */

interface SkillStatusBadgeProps {
  /** Top-level skill tags from SKILL.md frontmatter. */
  tags: string[] | undefined;
  /** Visual density. `badge` = text pill, `dot` = minimal status dot. */
  variant?: "badge" | "dot";
}

interface BadgeStyle {
  label: string;
  /** Tailwind classes for the pill surface (badge variant). */
  className: string;
  /** Tailwind fill for the status dot (dot variant). */
  dotClassName: string;
}

const KNOWN_TAGS: Record<string, BadgeStyle> = {
  experimental: {
    label: "Experimental",
    className: "border-amber-300 bg-amber-50 text-amber-900",
    dotClassName: "bg-amber-400",
  },
  "dev-tool": {
    label: "Dev tool",
    className: "border-sky-300 bg-sky-50 text-sky-900",
    dotClassName: "bg-sky-400",
  },
  "a2ui-demo": {
    label: "A2UI demo",
    className: "border-violet-300 bg-violet-50 text-violet-900",
    dotClassName: "bg-violet-400",
  },
  // Platform-embedded agents (Skill Studio copilot, help assistants). Hidden
  // from the switcher + marketplace entirely, so this badge only shows on
  // admin surfaces that list every skill — where "why is this here?" needs
  // an answer at a glance.
  system: {
    label: "System",
    className: "border-slate-300 bg-slate-50 text-slate-700",
    dotClassName: "bg-slate-400",
  },
};

export function SkillStatusBadge({ tags, variant = "badge" }: SkillStatusBadgeProps) {
  if (!tags || tags.length === 0) return null;
  const styled = tags.map((t) => KNOWN_TAGS[t]).filter((s): s is BadgeStyle => Boolean(s));
  if (styled.length === 0) return null;

  if (variant === "dot") {
    return (
      <span className="flex shrink-0 items-center gap-1">
        {styled.map((s) => (
          <span
            key={s.label}
            role="img"
            aria-label={s.label}
            title={s.label}
            className={cn("h-1.5 w-1.5 rounded-full", s.dotClassName)}
          />
        ))}
      </span>
    );
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {styled.map((s) => (
        <span
          key={s.label}
          className={`inline-flex items-center rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${s.className}`}
        >
          {s.label}
        </span>
      ))}
    </div>
  );
}
