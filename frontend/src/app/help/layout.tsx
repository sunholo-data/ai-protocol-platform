import type { ReactNode } from "react";

import { HelpGate } from "@/components/help/HelpGate";
import { HelpNav } from "@/components/help/HelpNav";
import { PageFrame } from "@/components/navigation/AppHeader";
import { BRANDING } from "@/lib/branding";
import { readManifest, translatedCards } from "@/lib/userGuide";

export const metadata = {
  title: `Help — ${BRANDING.appName}`,
  description: `How to use ${BRANDING.appName}.`,
};

/** /help — the user guide (docs/user-guide, bundled by scripts/user-guide/bundle.mjs). */
export default function HelpLayout({ children }: { children: ReactNode }) {
  const { pages } = readManifest();
  const cards = translatedCards();
  // Passed only when there are any, so an English-only deployment's payload is unchanged.
  const titles = Object.keys(cards).length
    ? { titles: Object.fromEntries(Object.entries(cards).map(([slug, t]) => [slug, Object.fromEntries(Object.entries(t).map(([l, c]) => [l, c.title]))])) }
    : {};
  return (
    <PageFrame>
    <HelpGate>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-8 md:flex-row md:gap-10 md:px-8">
        <HelpNav pages={pages} {...titles} />
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </HelpGate>
    </PageFrame>
  );
}
