"use client";

// Citation URI scheme links (e.g. inline-citation://doc/{docId}/block/{blockId})
// are embedded by the agent backend. This component renders them as a teal chip
// instead of a plain anchor. Clicking calls navigateToBlock.
// Security: only opens citation-scheme or https://storage.googleapis.com URLs.
// The scheme is configurable via NEXT_PUBLIC_CITATION_SCHEME (see branding.ts).

import { CITATION_SCHEME } from "@/lib/branding";

const CITATION_URI_RE = new RegExp(
  `^${CITATION_SCHEME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:\\/\\/doc\\/([^/]+)\\/block\\/([^/]+)$`,
);
const GCS_PREFIX = "https://storage.googleapis.com";

interface InlineCitationProps {
  href: string;
  children: React.ReactNode;
  navigateToBlock: (docId: string, blockId: string) => void;
}

export function InlineCitation({ href, children, navigateToBlock }: InlineCitationProps) {
  const match = href.match(CITATION_URI_RE);

  if (!match) {
    // Not a citation URI — only allow GCS URLs, nothing else
    const safeHref = href.startsWith(GCS_PREFIX) ? href : "#";
    return (
      <a href={safeHref} target="_blank" rel="noopener noreferrer" className="text-teal-600 underline">
        {children}
      </a>
    );
  }

  const [, docId, blockId] = match;

  function handleClick(e: React.MouseEvent) {
    e.preventDefault();
    navigateToBlock(docId, blockId);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className="inline-flex items-center gap-1 rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-xs font-medium text-teal-700 hover:bg-teal-100 transition-colors"
    >
      <svg
        className="h-3 w-3 shrink-0"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <path d="M4 8h8M8 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {children}
    </button>
  );
}

import React from "react";

// Regex to find citation-scheme links embedded as markdown-style [text](scheme://...) in plain text.
const INLINE_LINK_RE = new RegExp(
  `\\[([^\\]]+)\\]\\((${CITATION_SCHEME.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:\\/\\/[^)]+)\\)`,
  "g",
);

/**
 * Splits plain text on citation-scheme markdown links and returns an array of
 * React nodes — plain strings interleaved with InlineCitation chips.
 * Used by MessageBubble for the text rendering fallback (before ChatMarkdown exists).
 */
export function renderWithCitations(
  text: string,
  navigateToBlock: (docId: string, blockId: string) => void,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  INLINE_LINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = INLINE_LINK_RE.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const [, label, href] = match;
    nodes.push(
      <InlineCitation key={match.index} href={href} navigateToBlock={navigateToBlock}>
        {label}
      </InlineCitation>,
    );
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes.length > 0 ? nodes : [text];
}
