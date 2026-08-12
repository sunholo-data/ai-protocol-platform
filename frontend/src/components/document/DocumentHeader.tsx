import type { DocumentDetail } from "@/hooks/useDocument";

const FORMAT_COLORS: Record<string, string> = {
  pdf: "bg-red-100 text-red-700",
  docx: "bg-blue-100 text-blue-700",
  pptx: "bg-orange-100 text-orange-700",
  xlsx: "bg-green-100 text-green-700",
  csv: "bg-green-100 text-green-700",
  md: "bg-purple-100 text-purple-700",
  txt: "bg-muted text-muted-foreground",
};

interface DocumentHeaderProps {
  doc: DocumentDetail;
}

export function DocumentHeader({ doc }: DocumentHeaderProps) {
  const fmtColor =
    FORMAT_COLORS[doc.sourceFormat.toLowerCase()] ??
    "bg-muted text-muted-foreground";

  return (
    <div className="flex items-center gap-2 border-b px-3 py-2">
      <span className="min-w-0 flex-1 truncate text-sm font-medium">
        {doc.originalFilename}
      </span>
      {doc.sourceFormat && (
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${fmtColor}`}
        >
          {doc.sourceFormat}
        </span>
      )}
      {doc.sourceUrl && (
        <a
          href={doc.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
          title="Open original file"
        >
          ↗
        </a>
      )}
    </div>
  );
}
