"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useLocale } from "@/i18n";
import { pickVariant } from "@/i18n/resolve";

interface Props {
  pages: { slug: string; title: string }[];
  /** Translated titles, `{slug: {locale: title}}` — only when the deployment can show some. */
  titles?: Record<string, Record<string, string>>;
}

/**
 * The guide's contents beside an article. Not on the /help index: its cards
 * already list every topic, and two lists of the same nine is noise.
 */
export function HelpNav({ pages, titles }: Props) {
  const pathname = usePathname();
  const locale = useLocale();
  const title = (p: { slug: string; title: string }) => pickVariant(titles?.[p.slug], locale) ?? p.title;
  if (pathname === "/help") return null;
  const current = (slug: string) => (pathname === `/help/${slug}` ? "page" : undefined);
  return (
    <>
      {/* Phones: the contents fold into one control above the page. */}
      <details className="rounded-[var(--radius)] border border-border md:hidden">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-foreground">User guide contents</summary>
        <div className="space-y-1 px-1 pb-2">
          {pages.map((p) => (
            <Link
              key={p.slug}
              href={`/help/${p.slug}`}
              aria-current={current(p.slug)}
              className="block rounded-[var(--radius)] px-2 py-1.5 text-sm text-foreground hover:bg-muted aria-[current=page]:bg-muted aria-[current=page]:font-medium"
            >
              {title(p)}
            </Link>
          ))}
        </div>
      </details>
      <nav aria-label="User guide" className="hidden w-52 shrink-0 md:block">
        <div className="sticky top-6 space-y-1">
          <Link
            href="/help"
            className="block px-2 pb-2 font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground"
          >
            User guide
          </Link>
          {pages.map((p) => (
            <Link
              key={p.slug}
              href={`/help/${p.slug}`}
              aria-current={current(p.slug)}
              className="block rounded-[var(--radius)] px-2 py-1.5 text-sm text-foreground hover:bg-muted aria-[current=page]:bg-muted aria-[current=page]:font-medium"
            >
              {title(p)}
            </Link>
          ))}
        </div>
      </nav>
    </>
  );
}
