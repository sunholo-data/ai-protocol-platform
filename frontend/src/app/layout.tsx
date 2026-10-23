import type { Metadata, Viewport } from "next";
import { Montserrat } from "next/font/google";
import { AppUpdater } from "@/components/AppUpdater";
import { LocalModeBanner } from "@/components/LocalModeBanner";
import { BRANDING } from "@/lib/branding";
import { AppProviders } from "@/providers/AppProviders";
import "./globals.css";

/* The template's typeface (the Sunholo brand: Montserrat 400 for reading, 700
   for headings, 500–600 for labels). next/font downloads it at BUILD time and
   serves it from this origin — no runtime request to Google, no layout shift.
   Tailwind's `font-sans` reads --font-sans; a fork theme can still override
   font-family on body. */
const sans = Montserrat({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: BRANDING.appName,
  description: BRANDING.description,
  icons: {
    icon: BRANDING.logo.favicon,
    // iOS ignores the manifest's icons for Add to Home Screen and reads this;
    // without it the home-screen tile is a screenshot of the page.
    apple: "/images/logo/apple-touch-icon.png",
  },
  appleWebApp: { capable: true, title: BRANDING.appName, statusBarStyle: "default" },
};

/**
 * `themeColor` paints the status bar of the installed app. `viewport-fit` is
 * deliberately left at `auto`: iOS then keeps page content out of the notch
 * and home-indicator areas by itself. `cover` would draw edge-to-edge but make
 * every pinned surface responsible for its own `env(safe-area-inset-*)`
 * padding — the composers pad for the bottom inset already, so this is a
 * one-line switch once the header does too.
 */
export const viewport: Viewport = {
  themeColor: BRANDING.themeColor,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={sans.variable}>
      {BRANDING.themeStylesheet ? (
        <head>
          {/* Fork theme hook — see BRANDING.themeStylesheet. */}
          <link rel="stylesheet" href={BRANDING.themeStylesheet} />
        </head>
      ) : null}
      {/*
        The BODY owns the viewport; children get flex-1 (v6.19.0, AIPLA #23).

        Previously body was `min-h-screen` and any banner rendered as a sibling
        ABOVE the app shell — while the shell itself claimed `h-screen` (a full
        100vh). The banner's height was therefore additive, pushing the bottom
        of the shell (the chat input) below the fold. A first-time user had to
        scroll to find the box they were meant to type in.

        The robust shape is "body owns the viewport, children fill what's left":
        a banner takes its natural height and the shell gets the remainder, so
        it holds for ANY future banner (maintenance notice, access warning), not
        just the LOCAL_MODE one. `min-h-0` on the wrapper is what lets the shell
        shrink below its content height so its own internal scroll areas work.

        `h-dvh`, not `h-screen`: on iOS Safari `100vh` is the LARGE viewport
        (toolbar collapsed), so a 100vh body is taller than what is visible and
        the pinned composer sits under the toolbar. `100dvh` tracks the live
        viewport; on desktop the two are equal.
      */}
      <body className="font-sans bg-background text-foreground h-dvh flex flex-col antialiased">
        <AppUpdater />
        <LocalModeBanner />
        <div className="flex min-h-0 flex-1 flex-col">
          <AppProviders>{children}</AppProviders>
        </div>
      </body>
    </html>
  );
}
