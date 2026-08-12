import type { DocSummary } from "@/hooks/useDocument";

interface DocumentFooterProps {
  summary: DocSummary;
}

export function DocumentFooter({ summary }: DocumentFooterProps) {
  const stats = [
    { label: "blocks", value: summary.totalBlocks },
    { label: "tables", value: summary.tables },
    { label: "images", value: summary.images },
    { label: "changes", value: summary.changes },
  ].filter((s) => s.value > 0);

  if (stats.length === 0) return null;

  return (
    <div className="border-t px-3 py-1.5 text-[10px] text-muted-foreground">
      {stats.map((s, i) => (
        <span key={s.label}>
          {i > 0 && " · "}
          {s.value} {s.label}
        </span>
      ))}
    </div>
  );
}
