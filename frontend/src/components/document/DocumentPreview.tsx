"use client";

import type { ReactNode } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { FileText } from "lucide-react";
import { useDocument } from "@/hooks/useDocument";
import { DocumentThumbnail, type DocThumbnailSource } from "./DocumentThumbnail";
import { StructuredDocPreview } from "./StructuredDocPreview";

interface DocumentPreviewProps {
  source: DocThumbnailSource;
  /** Filename / caption shown under the preview image. */
  label?: string;
  /** The hover trigger — typically a file row or a document chip. */
  children: ReactNode;
  /** Side to open the preview on. Default "right". */
  side?: "top" | "right" | "bottom" | "left";
}

const IMAGE_OR_PDF = /^(pdf|png|jpe?g|gif|webp|bmp|tiff?)$/i;

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
  return <DocPreviewBody docId={source.docId} />;
}

function DocPreviewBody({ docId }: { docId: string }) {
  const { doc } = useDocument(docId);
  const fmt = (doc?.sourceFormat ?? "").toLowerCase();

  if (!doc) {
    return (
      <Frame>
        <div className="h-full w-full animate-pulse bg-muted/30" />
      </Frame>
    );
  }
  if (IMAGE_OR_PDF.test(fmt)) {
    return (
      <Frame>
        <DocumentThumbnail source={{ kind: "doc", docId }} width={400} />
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
            className="z-50 w-56 origin-[var(--radix-tooltip-content-transform-origin)] rounded-lg border border-border bg-popover p-2 shadow-lg duration-150 data-[state=delayed-open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=delayed-open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=delayed-open]:zoom-in-95"
          >
            <PreviewBody source={source} />
            {label ? <p className="mt-1.5 truncate text-xs text-muted-foreground">{label}</p> : null}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
