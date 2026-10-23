/**
 * The ONE look for anything that floats over the page: menus, popovers, hover
 * cards (tool-call pills, document previews), the skill switcher.
 *
 * Opaque by construction: `bg-popover` is defined in tailwind.config with a
 * fallback to the page background, so no fork theme can make it transparent.
 * Before this existed each popup spelled its own surface; two used an
 * undefined token and rendered see-through, with the text underneath bleeding
 * into them (pill hover cards, then the folder menu — 2026-10-02).
 *
 * Components add only their own size, padding and animation classes.
 * `src/__tests__/themeTokens.test.ts` fails on a Radix *.Content that doesn't
 * use this.
 */
export const FLOATING_SURFACE = "z-50 rounded-lg border border-border bg-popover text-popover-foreground shadow-lg";
