"use client";

import type { Block } from "@/components/document/BlocksRenderer";
import { cn } from "@/lib/utils";

/**
 * A compact, page-like preview rendered from a document's PARSED BLOCKS — for
 * formats we can't rasterize (docx, pptx, xlsx, md, txt…). Shows the first few
 * blocks styled like a miniature document so the hover preview conveys the
 * document's structure at a glance, with a soft fade at the bottom hinting more.
 *
 * Uses the ailang-parse block output already stored on the document, so there's
 * no extra render pipeline — and Firestore caches the doc, so re-hovering is
 * instant.
 */

const HEADING_STYLE = /^(title|subtitle|heading)/i;

function isHeading(b: Block): boolean {
  return b.type === "heading" || (b.style != null && HEADING_STYLE.test(b.style));
}

function PreviewBlock({ block }: { block: Block }) {
  if (isHeading(block)) {
    const level = block.level ?? 1;
    return (
      <p
        className={cn(
          "font-semibold text-neutral-800",
          level <= 1 ? "text-[11px]" : level === 2 ? "text-[10px]" : "text-[9px]",
        )}
      >
        {block.text}
      </p>
    );
  }

  if (block.type === "table") {
    const rows = (block.rows ?? []).slice(0, 3);
    const cols = block.headers?.length || rows[0]?.cells?.length || 0;
    return (
      <div className="my-0.5 overflow-hidden rounded-sm border border-neutral-200">
        {block.headers && block.headers.length > 0 && (
          <div className="flex bg-neutral-100">
            {block.headers.slice(0, 4).map((h, i) => (
              <span key={i} className="flex-1 truncate border-r border-neutral-200 px-1 py-0.5 text-[7px] font-medium">
                {h.text}
              </span>
            ))}
          </div>
        )}
        {rows.map((r, ri) => (
          <div key={ri} className="flex border-t border-neutral-100">
            {(r.cells ?? []).slice(0, 4).map((c, ci) => (
              <span key={ci} className="flex-1 truncate border-r border-neutral-100 px-1 py-0.5 text-[7px]">
                {c.text}
              </span>
            ))}
          </div>
        ))}
        {cols === 0 && <div className="px-1 py-0.5 text-[7px] text-neutral-400">table</div>}
      </div>
    );
  }

  if (block.type === "list" || (block.items && block.items.length > 0)) {
    return (
      <ul className="ml-2 list-disc space-y-0.5">
        {(block.items ?? []).slice(0, 4).map((it, i) => (
          <li key={i} className="truncate text-[8px] text-neutral-600">
            {it}
          </li>
        ))}
      </ul>
    );
  }

  if (block.text) {
    return <p className="line-clamp-2 text-[8px] leading-snug text-neutral-600">{block.text}</p>;
  }
  return null;
}

interface StructuredDocPreviewProps {
  blocks: Block[];
  /** How many leading blocks to show. Default 16. */
  limit?: number;
  className?: string;
}

export function StructuredDocPreview({ blocks, limit = 16, className }: StructuredDocPreviewProps) {
  const shown = blocks.slice(0, limit);
  return (
    <div className={cn("relative h-full w-full overflow-hidden bg-white p-3", className)}>
      <div className="space-y-1">
        {shown.map((b, i) => (
          <PreviewBlock key={i} block={b} />
        ))}
      </div>
      {/* soft fade hinting there's more of the document below */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white to-transparent" />
    </div>
  );
}
