import type { MetadataRoute } from "next";

import { BRANDING } from "@/lib/branding";

/**
 * Web app manifest — served at /manifest.webmanifest by the App Router. Makes
 * "Add to Home Screen" open the app full-screen (`standalone`) with its own
 * icon. Icons follow the logo convention: swap the PNGs in
 * /public/images/logo/ (regenerate with `make pwa-icons`).
 *
 * `orientation` is deliberately unset: chat reads best in portrait, wide
 * workbench surfaces in landscape — locking either is wrong.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: BRANDING.appName,
    short_name: BRANDING.appName,
    description: BRANDING.description,
    start_url: "/",
    scope: "/",
    display: "standalone",
    theme_color: BRANDING.themeColor,
    background_color: BRANDING.themeColor,
    icons: [
      { src: "/images/logo/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/images/logo/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // A separate asset, not the same tile: Android crops maskable icons to
      // its own shape, so the glyph sits inside the inner safe zone.
      { src: "/images/logo/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
