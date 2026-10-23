import type { ReactNode } from "react";
import Link from "next/link";

import { HelpGate } from "@/components/help/HelpGate";
import { BRANDING } from "@/lib/branding";
import { readManifest } from "@/lib/userGuide";

export const metadata = {
  title: `Help — ${BRANDING.appName}`,
  description: `How to use ${BRANDING.appName}.`,
};

/** /help — the user guide (docs/user-guide, bundled by scripts/user-guide/bundle.mjs). */
export default function HelpLayout({ children }: { children: ReactNode }) {
  const { pages } = readManifest();
  return (
    <HelpGate>
      <div className="mx-auto flex w-full max-w-6xl gap-10 px-4 py-8 md:px-8">
        <nav aria-label="User guide" className="hidden w-52 shrink-0 md:block">
          <div className="sticky top-6 space-y-1">
            <Link href="/help" className="block px-2 pb-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground">
              User guide
            </Link>
            {pages.map((p) => (
              <Link
                key={p.slug}
                href={`/help/${p.slug}`}
                className="block rounded-[var(--radius)] px-2 py-1.5 text-sm text-foreground hover:bg-muted"
              >
                {p.title}
              </Link>
            ))}
          </div>
        </nav>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </HelpGate>
  );
}
