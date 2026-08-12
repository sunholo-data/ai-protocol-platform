import type { Metadata } from "next";
import { LocalModeBanner } from "@/components/LocalModeBanner";
import { BRANDING } from "@/lib/branding";
import { AppProviders } from "@/providers/AppProviders";
import "./globals.css";

export const metadata: Metadata = {
  title: BRANDING.appName,
  description: BRANDING.description,
  icons: {
    icon: BRANDING.logo.favicon,
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
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
      */}
      <body className="font-sans bg-background text-foreground h-screen flex flex-col antialiased">
        <LocalModeBanner />
        <div className="flex min-h-0 flex-1 flex-col">
          <AppProviders>{children}</AppProviders>
        </div>
      </body>
    </html>
  );
}
