/**
 * The owner id every platform-seeded skill carries (backend/skills/platform.py
 * `PLATFORM_OWNER_UID`). It is stored data in every fork's Firestore, so it
 * can't be renamed — but it needn't be what users read in the address bar.
 *
 * `NEXT_PUBLIC_PLATFORM_OWNER_ALIAS` (e.g. `sunholo`) is the friendly name the
 * URL shows instead: `/chat/@sunholo/web-researcher` rather than
 * `/chat/@aitana-platform/web-researcher`. Friendly on output, resolved back
 * to the canonical id on input (repo principle #9) — and the canonical form
 * keeps resolving, so existing links and bookmarks don't break. Unset means
 * URLs show the raw id, as before.
 */
export const PLATFORM_OWNER_UID = "aitana-platform";

// G20: `||` — Docker pre-declares NEXT_PUBLIC_* as "" when unset.
const PLATFORM_OWNER_ALIAS = (process.env.NEXT_PUBLIC_PLATFORM_OWNER_ALIAS || "").trim();

/** Owner id → the segment shown in `/chat/@{segment}/{slug}`. */
export function ownerUrlSegment(ownerId: string): string {
  return ownerId === PLATFORM_OWNER_UID && PLATFORM_OWNER_ALIAS ? PLATFORM_OWNER_ALIAS : ownerId;
}

/** A `/chat/@{segment}` segment (without the `@`) → the canonical owner id. */
export function ownerIdFromUrlSegment(segment: string): string {
  return PLATFORM_OWNER_ALIAS && segment === PLATFORM_OWNER_ALIAS ? PLATFORM_OWNER_UID : segment;
}
