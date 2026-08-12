import { cn } from "@/lib/utils";

/**
 * Opinionated label/value list primitive.
 *
 * Solves the cramped-label problem (G29 — template-chat-surface-defaults.md)
 * that bit every audit pane and embedded JSON card in downstream forks:
 * A2UI's Row component has no convention for a fixed-width label column,
 * so long labels like "Invoice Number" wrap onto two lines and crowd the
 * value. This component takes that decision out of the renderer's hands —
 * labels live in a fixed-width column (120-160px), values flow on the
 * right with monospace tabular-nums when numeric, right-aligned so money
 * columns line up like an actual ledger.
 *
 * Used in:
 *  - InputOutputCard (audit) — every specialist's input/output
 *  - JsonAsStructuredCard (chat) — inline scalar-heavy JSON payloads
 *  - Workbench per-card views — extracted/structured payloads
 *
 * Tone: pass `tone="dense"` for compact rows in inspector sub-cards;
 * default `"comfortable"` is for primary surfaces.
 *
 * Ported from gde-ap-agent fork 2026-06-05; the workspace-card-shape
 * convention this enforces is template-agnostic.
 */

export interface DefinitionItem {
  /** Label rendered in the left column. */
  label: string;
  /** Value can be a primitive or a React node (for chips, links, etc.). */
  value: React.ReactNode;
  /** Tailwind classes applied to the value cell. Use to force monospace,
   * destructive text, etc. without overriding the whole row. */
  valueClassName?: string;
  /** Override label column for this one row (rare — pass when one label
   * is unusually wide and you'd rather have it span more). */
  labelClassName?: string;
}

interface DefinitionListProps {
  items: DefinitionItem[];
  tone?: "comfortable" | "dense";
  className?: string;
  /** When true, every value is treated as numeric: monospace + tabular-nums
   * + right-aligned. Use for tables of amounts. */
  numeric?: boolean;
}

export function DefinitionList({
  items,
  tone = "comfortable",
  numeric = false,
  className,
}: DefinitionListProps) {
  if (items.length === 0) return null;
  return (
    <dl
      className={cn(
        "grid",
        // Two-column grid: label fixed, value flexible. The min-w-0 on the
        // value column lets long values truncate or wrap on their own
        // terms instead of pushing the label.
        "grid-cols-[minmax(120px,160px)_minmax(0,1fr)]",
        tone === "comfortable" ? "gap-y-2" : "gap-y-1",
        className,
      )}
    >
      {items.map((item, i) => (
        <div key={`${item.label}-${i}`} className="contents">
          <dt
            className={cn(
              "text-xs leading-snug text-muted-foreground",
              tone === "comfortable" ? "py-1" : "",
              item.labelClassName,
            )}
          >
            {item.label}
          </dt>
          <dd
            className={cn(
              "min-w-0 break-words text-sm leading-snug text-foreground",
              tone === "comfortable" ? "py-1" : "",
              numeric && "font-mono text-right tabular-nums",
              item.valueClassName,
            )}
          >
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
