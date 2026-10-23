/**
 * One user guide page as JSON, for the workbench Help tab (HelpArtefactTab).
 *
 * Generated at build time from the same bundle as /help/<slug>, so the tab shows
 * exactly the published page: the tab is told WHICH page to open, never sent the
 * page's text. Not behind HelpGate: the guide is synthetic and already in the
 * /help page payload.
 */

import { NextResponse } from "next/server";

import { readManifest, readPage } from "@/lib/userGuide";

export const dynamic = "force-static";
export const dynamicParams = false;

export function generateStaticParams() {
  return readManifest().pages.map((p) => ({ slug: p.slug }));
}

export async function GET(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const page = readPage((await params).slug);
  if (!page) return NextResponse.json({ error: "No such help page" }, { status: 404 });
  const { meta, body } = page;
  return NextResponse.json({ slug: meta.slug, title: meta.title, summary: meta.summary, body });
}
