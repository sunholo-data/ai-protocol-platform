import { cn } from "@/lib/utils";

/**
 * The one control style for the header: 44px touch target on phones, 36px from
 * `md`, quiet 1px border, the theme radius, and a visible keyboard focus ring.
 * Shared with SkillSwitcher and UserMenu so every button in the bar matches.
 */
export const headerControl =
  "inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-[var(--radius)] border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring md:h-9";
/** Icon-only variant: square. */
export const headerIconControl = cn(headerControl, "w-11 px-0 md:w-9");
