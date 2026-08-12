"use client";

import type { DocTabData } from "@/components/doc-browser/DocTab";

/**
 * Derive the list of document ids that should be sent in the LLM-context
 * payload (`forwardedProps.document_ids`) for the next chat turn.
 *
 * The chat page used to inline this as
 * `openTabs.filter((t) => t.included).map((t) => t.id)` — a one-liner that
 * was never directly tested. Extracted here so the multi-doc contract
 * (locked by `multi-doc-context-fix.md` / 1.22) has a regression test that
 * lives next to the code, not behind a chat-page integration harness.
 *
 * Rules:
 * - A tab contributes its id IFF its `included` flag is truthy.
 * - Order is preserved (matches the visual tab order in the bar above the
 *   chat).
 * - Duplicates are kept (defensive — duplicates indicate a state bug
 *   elsewhere; surfacing them is more useful than silently masking).
 * - Empty input → empty array (the backend treats absence as "no docs"
 *   per chat-history-fixes B2).
 */
export function computeIncludedDocIds(openTabs: DocTabData[]): string[] {
  return openTabs.filter((t) => t.included).map((t) => t.id);
}
