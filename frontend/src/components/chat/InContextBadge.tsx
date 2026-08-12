import type { DocTabData } from "@/components/doc-browser/DocTab";

interface InContextBadgeProps {
  openTabs: DocTabData[];
  includedDocIds: string[];
}

/**
 * In-context caption above the chat input (v6.4.0 INTERNAL-SHELL M3).
 *
 * Renders "Will process: filename.pdf" for a single included doc, or
 * "Will process N documents on next turn" for multi-doc context. Returns
 * null when nothing is included so the composer area stays uncluttered.
 *
 * Without this caption, multi-doc context is ambiguous — if the user has
 * 3 open tabs and 1 unchecked, they can't tell what the agent will see.
 *
 * Ported from gde-ap-agent app/chat/[...path]/page.tsx lines 626–645.
 */
export function InContextBadge({ openTabs, includedDocIds }: InContextBadgeProps) {
  if (includedDocIds.length === 0) return null;
  const includedTabs = openTabs.filter((t) => includedDocIds.includes(t.id));
  const label =
    includedTabs.length === 1
      ? `Will process: ${includedTabs[0].filename}`
      : `Will process ${includedDocIds.length} documents on next turn`;
  return (
    <div className="mb-2 flex items-center gap-2 px-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
      <span className="h-1.5 w-1.5 rounded-full bg-primary/70" aria-hidden />
      <span className="truncate">{label}</span>
    </div>
  );
}
