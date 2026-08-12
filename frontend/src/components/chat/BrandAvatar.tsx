"use client";

import { BRANDING } from "@/lib/branding";

/**
 * Bot-side chat avatar — the brand logo rendered as a clean 28px circle,
 * mirroring the user avatar (same h-7 w-7 rounded-full border) so the two
 * sides of the conversation line up symmetrically.
 *
 * The logo fills the circle (object-cover) rather than floating small inside
 * a tinted ring — brand marks are usually already circular, so a second ring
 * looked off. A subtle border keeps a light-on-white edge crisp.
 */
export function BrandAvatar() {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={BRANDING.logo.chatAvatar}
      alt={BRANDING.appName}
      className="h-7 w-7 shrink-0 rounded-full border border-border bg-background object-cover"
    />
  );
}
