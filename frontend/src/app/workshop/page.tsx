/**
 * /workshop — public route rendering the repo's WORKSHOP.md.
 *
 * The LocalModeBanner deep-links here (#graduating-from-local-mode) so the
 * banner's "Connect to your own GCP →" affordance lands on the canonical
 * walkthrough. Reading the markdown directly from disk keeps a single
 * source of truth — anything updated in WORKSHOP.md flows through with no
 * code change.
 */

import fs from "node:fs/promises";
import path from "node:path";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { BRANDING } from "@/lib/branding";

export const metadata = {
  title: `Workshop — ${BRANDING.appName}`,
  description: "Quick-start guide for running the platform locally + graduating to your own GCP project.",
};

async function loadWorkshopMd(): Promise<string> {
  // process.cwd() in `next dev` and `next start` is the frontend dir; the
  // markdown sits at repo root. Walk up one level.
  const filePath = path.join(process.cwd(), "..", "WORKSHOP.md");
  try {
    return await fs.readFile(filePath, "utf-8");
  } catch (err) {
    console.error("Failed to load WORKSHOP.md from", filePath, err);
    return "# Workshop guide unavailable\n\nThe WORKSHOP.md file could not be loaded. See the repo root for the latest version.";
  }
}

export default async function WorkshopPage() {
  const md = await loadWorkshopMd();
  return (
    <main className="max-w-3xl mx-auto px-6 py-12">
      <article className="prose prose-slate dark:prose-invert max-w-none prose-headings:scroll-mt-20">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
      </article>
    </main>
  );
}
