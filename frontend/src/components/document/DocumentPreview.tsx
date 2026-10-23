"use client";

import type { ReactNode } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { FileText } from "lucide-react";
import { useDocument } from "@/hooks/useDocument";
import { DocumentThumbnail, isThumbnailableFormat, type DocThumbnailSource } from "./DocumentThumbnail";
import { StructuredDocPreview } from "./StructuredDocPreview";
import { FLOATING_SURFACE } from "@/lib/surfaces";

interface DocumentPreviewProps {
  source: DocThumbnailSource;
  /** Filename / caption shown under the preview image. */
  label?: string;
  /** The hover trigger — typically a file row or a document chip. */
  children: ReactNode;
  /** Side to open the preview on. Default "right". */
  side?: "top" | "right" | "bottom" | "left";
}

/** Common aspect-3/4 frame for whatever preview we render. */
function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="aspect-[3/4] w-full overflow-hidden rounded-md border border-border bg-white">{children}</div>
  );
}

/**
 * Renders the right preview for a document source:
 * - bucket ref → rendered image thumbnail (PDF page 1 / image).
 * - imported doc → image thumbnail for PDFs/images; a structured block preview
 *   for docparse'd formats (docx, pptx, xlsx, md…); an icon otherwise.
 * Mounts only when the tooltip opens, so it's lazy.
 */
function PreviewBody({ source }: { source: DocThumbnailSource }) {
  if (source.kind === "bucket") {
    return (
      <Frame>
        <DocumentThumbnail source={source} width={400} />
      </Frame>
    );
  }
  // The caller already knows it's a PDF/image (a list row) → straight to the
  // owner-checked thumbnail route; no need to load the full record + blocks.
  if (isThumbnailableFormat(source.format)) {
    return (
      <Frame>
        <DocumentThumbnail source={source} width={400} />
      </Frame>
    );
  }
  return <DocPreviewBody source={source} />;
}

function Unavailable() {
  return (
    <Frame>
      <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-muted-foreground">
        <FileText className="h-8 w-8" aria-hidden="true" />
        <span className="text-[11px]">Preview unavailable</span>
      </div>
    </Frame>
  );
}

function DocPreviewBody({ source }: { source: Extract<DocThumbnailSource, { kind: "doc" }> }) {
  const { doc, error } = useDocument(source.docId);
  const fmt = (doc?.sourceFormat ?? "").toLowerCase();

  if (!doc && error) return <Unavailable />;
  if (!doc) {
    return (
      <Frame>
        <div className="h-full w-full animate-pulse bg-muted/30" />
      </Frame>
    );
  }
  if (isThumbnailableFormat(fmt)) {
    return (
      <Frame>
        <DocumentThumbnail source={{ ...source, format: fmt }} width={400} />
      </Frame>
    );
  }
  if (doc.blocks.length > 0) {
    return (
      <Frame>
        <StructuredDocPreview blocks={doc.blocks} />
      </Frame>
    );
  }
  return (
    <Frame>
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <FileText className="h-8 w-8" aria-hidden="true" />
      </div>
    </Frame>
  );
}

/**
 * Hover preview popover for a document. Wraps any element; on hover it shows a
 * floating card with a first-page image (PDFs/images) or a structured block
 * preview (docparse'd docs) + filename.
 *
 * Lazy by design: the preview only mounts (and fetches) when the tooltip opens,
 * so long file lists don't fire a request per row. Built on Radix Tooltip so it
 * inherits hover-intent delay, keyboard focus, and collision handling.
 */
export function DocumentPreview({ source, label, children, side = "right" }: DocumentPreviewProps) {
  return (
    <Tooltip.Provider delayDuration={250} skipDelayDuration={100}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            side={side}
            sideOffset={8}
            collisionPadding={12}
            // Radix also renders the content into a visually-hidden a11y node;
            // without an aria-label that copy re-mounts the preview body, so
            // every hover fetched the thumbnail twice.
            aria-label={label ? `Preview of ${label}` : "Document preview"}
            className={`${FLOATING_SURFACE} w-56 origin-[var(--radix-tooltip-content-transform-origin)] p-2 duration-150 data-[state=delayed-open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=delayed-open]:zoom-in-95`}
          >
            <PreviewBody source={source} />
            {label ? <p className="mt-1.5 truncate text-xs text-muted-foreground">{label}</p> : null}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
