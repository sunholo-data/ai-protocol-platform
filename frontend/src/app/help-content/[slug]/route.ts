/**
 * One user guide page as JSON, for the workbench Help tab (HelpArtefactTab).
 *
 * Generated at build time from the same bundle as /help/<slug>, so the tab shows
 * exactly the published page: the tab is told WHICH page to open, never sent the
 * page's text. Not behind HelpGate: the guide is synthetic and already in the
 * /help page payload.
 *
 * `translations` ({locale: {title, summary, body}}) is added only when the
 * deployment can show a translation of the page (multi-language.md §5); the tab
 * picks the reader's language. An English-only deployment's JSON is unchanged.
 */

import { NextResponse } from "next/server";

import { pageTranslations, readManifest, readPage } from "@/lib/userGuide";

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return readManifest().pages.map((p) => ({ slug: p.slug }));
}

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const page = readPage((await params).slug);
  if (!page) return NextResponse.json({ error: "No such help page" }, { status: 404 });
  const { meta, body } = page;
  const translations = pageTranslations(meta.slug);
  return NextResponse.json({
    slug: meta.slug,
    title: meta.title,
    summary: meta.summary,
    body,
    ...(translations.length
      ? { translations: Object.fromEntries(translations.map((t) => [t.locale, { title: t.meta.title, summary: t.meta.summary, body: t.body }])) }
      : {}),
  });
}
