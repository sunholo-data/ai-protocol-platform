"use client";

import { useEffect, useState } from "react";
import { BlocksRenderer } from "./BlocksRenderer";
import type { DocumentDetail } from "@/hooks/useDocument";
import { fetchWithAuth } from "@/lib/apiClient";

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
function usePdfPreviewBlobUrl(docId: string): { url: string | null; error: string | null } {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setError(null);
    setUrl(null);

    (async () => {
      try {
        const res = await fetchWithAuth(
          `/api/proxy/api/documents/${encodeURIComponent(docId)}/preview`,
        );
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
  }, [docId]);

  return { url, error };
}

function PdfPreview({ doc }: { doc: DocumentDetail }) {
  const { url, error } = usePdfPreviewBlobUrl(doc.id);

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

export function DocumentViewer({ doc }: DocumentViewerProps) {
  if (PDF_PREVIEW_FORMATS.has(doc.sourceFormat)) {
    return <PdfPreview doc={doc} />;
  }

  if (!doc.blocks || doc.blocks.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
        Document preview unavailable.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <BlocksRenderer blocks={doc.blocks} />
    </div>
  );
}
