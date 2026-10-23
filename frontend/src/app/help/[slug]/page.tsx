import Link from "next/link";
import { notFound } from "next/navigation";

import { GuideBody } from "@/components/help/GuideBody";
import { BRANDING } from "@/lib/branding";
import { readManifest, readPage } from "@/lib/userGuide";
import { withAppName } from "@/lib/userGuideParse";

export const dynamicParams = false;

export function generateStaticParams() {
  return readManifest().pages.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const page = readPage((await params).slug);
  return { title: page ? `${withAppName(page.meta.title, BRANDING.appName)} — Help` : "Help" };
}

export default async function HelpPage({ params }: { params: Promise<{ slug: string }> }) {
  const page = readPage((await params).slug);
  if (!page) notFound();
  const { meta, body } = page;
  const toc = meta.sections.filter((s) => s.level === 2);
  return (
    <article className="space-y-6">
      <header className="space-y-2">
        <Link href="/help" className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground hover:text-foreground">
          User guide
        </Link>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">{withAppName(meta.title, BRANDING.appName)}</h1>
        {meta.summary ? <p className="max-w-prose text-lg text-muted-foreground">{withAppName(meta.summary, BRANDING.appName)}</p> : null}
      </header>
      {toc.length > 1 ? (
        <nav aria-label="On this page" className="border-y border-border py-3">
          <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
            {toc.map((s) => (
              <li key={s.anchor}>
                <a href={`#${s.anchor}`} className="font-medium text-primary hover:underline">
                  {withAppName(s.title, BRANDING.appName)}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}
      <GuideBody body={body} appName={BRANDING.appName} />
    </article>
  );
}
