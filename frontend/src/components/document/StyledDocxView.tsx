"use client";

// v6.34.0 DOC-WORKBENCH — show the document as it actually looks.
//
// The block view (`BlocksRenderer`) renders the parsed BlockADT: headings,
// paragraphs, tables, lists. That is deliberate and it is what the AI sees —
// but it carries no styling, because styling lives in the .docx theme and
// styles parts, which the block model does not model at all.
//
// Consequence, reported on test 2026-09-02: applying a letterhead changed the
// file and changed nothing on screen. The feature was real and invisible.
//
// This renders the actual .docx with `docx-preview`, so fonts, colours, heading
// styles, tables, headers and footers appear as Word would draw them. The bytes
// come through the SAME authenticated route as everything else — never a public
// or signed URL (CLAUDE.md security rule).
//
// Text selection still works: docx-preview writes real DOM, so the
// select-a-passage-and-edit flow works from this view exactly as it does from
// the block view.

import { useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { documentPreviewUrl } from "@/lib/documentPreview";

interface StyledDocxViewProps {
  docId: string;
  /** Changes whenever the document is re-ingested, forcing a re-render. */
  freshness: string;
}

export function StyledDocxView({ docId, freshness }: StyledDocxViewProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        // `freshness` goes in the URL, not just the deps — see documentPreview.ts.
        const res = await fetchWithAuth(documentPreviewUrl(docId, freshness));
        if (cancelled) return;
        if (!res.ok) {
          setError(
            res.status === 403
              ? "You don't have access to this document."
              : `Couldn't load the styled view (${res.status})`,
          );
          return;
        }
        const buffer = await res.arrayBuffer();
        if (cancelled || !hostRef.current) return;

        // Loaded on demand: ~1 MB of renderer that most sessions never need.
        const { renderAsync } = await import("docx-preview");
        if (cancelled || !hostRef.current) return;

        hostRef.current.innerHTML = "";
        await renderAsync(buffer, hostRef.current, undefined, {
          className: "docx",
          inWrapper: true,
          ignoreWidth: false,
          ignoreHeight: true,
          breakPages: true,
          experimental: true,
        });
        if (!cancelled) setError(null);
      } catch (err) {
        if (!cancelled) {
          // A renderer that chokes on one document must not leave a blank pane
          // with no explanation (NEVER-SILENT #8) — the Text view still works.
          setError(err instanceof Error ? err.message : "Couldn't render this document.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [docId, freshness]);

  return (
    <div className="min-h-0 flex-1 overflow-auto bg-muted/30 p-3">
      {loading && <p className="p-3 text-xs text-muted-foreground">Rendering the document…</p>}
      {error && (
        <p className="p-3 text-xs text-destructive" role="alert">
          {error} Switch to the Text view to read it.
        </p>
      )}
      <div ref={hostRef} className="docx-host [&_.docx-wrapper]:bg-transparent [&_.docx-wrapper]:p-0" />
    </div>
  );
}
