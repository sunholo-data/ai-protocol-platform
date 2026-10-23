"use client";

import { useEffect, useState } from "react";
import { BlocksRenderer } from "./BlocksRenderer";
import { StyledDocxView } from "./StyledDocxView";
import type { DocumentDetail } from "@/hooks/useDocument";
import { fetchWithAuth } from "@/lib/apiClient";
import { documentPreviewUrl } from "@/lib/documentPreview";

interface DocumentViewerProps {
  doc: DocumentDetail;
}

// PDFs (and any other format where docparse can't extract structural blocks)
// get a native browser preview via /api/documents/{id}/preview — the backend
// streams the original bytes inline. The agent still consumes doc.blocks for
// grounding/citations; this just gives humans a faithful visual rendering
// instead of the flat-text-block view that's especially ugly for PDFs.
//
// All other formats (.docx, .pptx, .xlsx, .odt, .html, .md, etc.) get the
// rich block-based rendering — headings/tables/lists/track-changes — which
// looks far better than an iframe of the raw .docx zip would.
const PDF_PREVIEW_FORMATS = new Set(["pdf", "PDF"]);

/**
 * usePdfPreviewBlobUrl — fetch the PDF bytes through the authenticated
 * proxy (`fetchWithAuth` attaches the Firebase Bearer that the backend
 * route requires), wrap in a Blob URL, and hand it to the iframe.
 *
 * Why: iframes don't carry custom request headers. A naked
 * `<iframe src="/api/proxy/api/documents/{id}/preview">` reaches the
 * backend with NO Authorization, gets a "Missing Authorization header"
 * 401, and never renders. The fetch-then-Blob pattern moves the auth
 * step into JS (which can set headers) and gives the iframe a
 * `blob:` URL that doesn't need auth at all.
 *
 * Trade-off: loses streaming — the entire PDF must finish downloading
 * before the iframe paints. For demo-scale PPAs (~500KB-2MB) the wait
 * is tens of ms, fine. If a future need pushes this to multi-hundred-
 * megabyte docs, swap to a signed-URL pattern.
 *
 * Cleanup: returned object URL is revoked on unmount + on docId change
 * to avoid leaking blobs into browser memory across doc switches.
 */
function usePdfPreviewBlobUrl(docId: string, freshness: string): { url: string | null; error: string | null } {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setError(null);
    setUrl(null);

    (async () => {
      try {
        const res = await fetchWithAuth(documentPreviewUrl(docId, freshness));
        if (!res.ok) {
          if (!cancelled) setError(`Preview unavailable (HTTP ${res.status})`);
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setUrl(createdUrl);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
    // `freshness` is in the deps on purpose. Regeneration keeps the SAME docId
    // (that is what makes an edit update in place), so a `[docId]`-only effect
    // never refetched — the iframe kept showing the bytes from before the edit
    // and the document looked unchanged no matter what you did to it.
  }, [docId, freshness]);

  return { url, error };
}

function PdfPreview({ doc }: { doc: DocumentDetail }) {
  // parsedAt moves on every re-ingest, so it is the document's freshness token.
  const { url, error } = usePdfPreviewBlobUrl(doc.id, doc.parsedAt ?? "");

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        {error}
      </div>
    );
  }
  if (!url) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Loading preview…
      </div>
    );
  }
  // Iframe inside a flex column inside an overflow-auto ancestor doesn't
  // reliably stretch via `h-full` / `flex-1` — browsers fall back to the
  // iframe's intrinsic content height, which is ~150px for a fresh PDF.
  // Wrap in a relative flex-1 container + position the iframe absolutely
  // to inset-0 so it fills whatever vertical room the workbench gives
  // the doc tab, regardless of the iframe's own content-driven sizing.
  // Add a generous min-h so the preview still has real estate even if a
  // parent's flex math goes wrong.
  return (
    <div className="relative w-full min-h-[60vh] flex-1">
      <iframe
        title={doc.originalFilename}
        src={url}
        className="absolute inset-0 h-full w-full border-0"
      />
    </div>
  );
}

// Formats `docx-preview` can draw. Everything else keeps the block view, which
// is the only one that exists for them.
const STYLED_VIEW_FORMATS = new Set(["docx", "DOCX"]);

export function DocumentViewer({ doc }: DocumentViewerProps) {
  const canShowStyled = STYLED_VIEW_FORMATS.has(doc.sourceFormat);
  // Styled by default where it exists: the block view carries no styling, so
  // applying a template used to change the file and nothing on screen. The Text
  // view stays one click away — it is what the AI reads, and it is the fallback
  // when a document defeats the renderer.
  const [styled, setStyled] = useState(true);

  if (PDF_PREVIEW_FORMATS.has(doc.sourceFormat)) {
    return <PdfPreview doc={doc} />;
  }

  const hasBlocks = Boolean(doc.blocks && doc.blocks.length > 0);
  if (!hasBlocks && !canShowStyled) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Document preview unavailable.
      </div>
    );
  }

  const showStyled = canShowStyled && styled;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {canShowStyled && (
        <div className="flex shrink-0 items-center gap-1 border-b bg-muted/20 px-3 py-1">
          {(
            [
              ["Styled", true, "How the document looks, with its fonts and formatting"],
              ["Text", false, "The plain structure — this is what the assistant reads"],
            ] as const
          ).map(([label, value, hint]) => (
            <button
              key={label}
              type="button"
              onClick={() => setStyled(value)}
              title={hint}
              aria-pressed={styled === value}
              className={`rounded px-2 py-0.5 text-[11px] ${
                styled === value
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {showStyled ? (
        <StyledDocxView docId={doc.id} freshness={doc.parsedAt ?? ""} />
      ) : hasBlocks ? (
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <BlocksRenderer blocks={doc.blocks} />
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
          No text content was extracted from this document.
        </div>
      )}
    </div>
  );
}
