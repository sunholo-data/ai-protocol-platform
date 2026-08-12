import type { ReactNode } from "react";

export interface BlockCell {
  text?: string;
}

export interface BlockRow {
  cells?: BlockCell[];
}

export interface Block {
  type: string;
  text?: string;
  level?: number;
  style?: string;
  change_type?: string;
  author?: string;
  date?: string;
  headers?: BlockCell[];
  rows?: BlockRow[];
  items?: string[];
  ordered?: boolean;
  description?: string;
  transcription?: string;
  mime?: string;
  kind?: string;
  children?: Block[];
}

const HEADING_STYLE_LEVEL: Record<string, number> = {
  Title: 1,
  "Heading 1": 1,
  Subtitle: 2,
  "Heading 2": 2,
  "Heading 3": 3,
  "Heading 4": 4,
  "Heading 5": 5,
  "Heading 6": 6,
};

function headingClass(level: number): string {
  switch (level) {
    case 1: return "mt-4 mb-2 text-xl font-semibold tracking-tight";
    case 2: return "mt-3 mb-1.5 text-lg font-semibold";
    case 3: return "mt-2 mb-1 text-base font-semibold";
    case 4: return "mt-2 mb-1 text-sm font-semibold";
    default: return "mt-1.5 mb-0.5 text-sm font-medium";
  }
}

function renderHeading(level: number, text: string): ReactNode {
  const cls = headingClass(level);
  switch (Math.min(Math.max(level, 1), 6)) {
    case 1: return <h1 className={cls}>{text}</h1>;
    case 2: return <h2 className={cls}>{text}</h2>;
    case 3: return <h3 className={cls}>{text}</h3>;
    case 4: return <h4 className={cls}>{text}</h4>;
    case 5: return <h5 className={cls}>{text}</h5>;
    default: return <h6 className={cls}>{text}</h6>;
  }
}

function renderText(b: Block): ReactNode {
  const text = b.text ?? "";
  const headingLevel = b.style ? HEADING_STYLE_LEVEL[b.style] : undefined;
  if (headingLevel) return renderHeading(headingLevel, text);

  if (b.style === "abstract") {
    return <p className="my-2 italic text-sm text-muted-foreground border-l-2 border-muted pl-3">{text}</p>;
  }
  if (b.style === "bibitem") {
    return <p className="my-1 text-xs text-muted-foreground pl-4 -indent-4">{text}</p>;
  }
  if (b.style === "equation" || b.style === "equation-display") {
    return <pre className="my-2 px-3 py-2 bg-muted/50 rounded text-xs font-mono whitespace-pre-wrap">{text}</pre>;
  }
  if (!text.trim()) return null;
  return <p className="my-1 text-sm leading-relaxed">{text}</p>;
}

function renderTable(b: Block): ReactNode {
  const headers = b.headers ?? [];
  const rows = b.rows ?? [];
  return (
    <div className="my-2 overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        {headers.length > 0 && (
          <thead>
            <tr className="bg-muted/50">
              {headers.map((h, i) => (
                <th key={i} className="border px-2 py-1 text-left font-semibold">
                  {h.text ?? ""}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} className="border-b last:border-b-0">
              {(row.cells ?? []).map((c, ci) => (
                <td key={ci} className="border px-2 py-1 align-top">
                  {c.text ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderList(b: Block): ReactNode {
  const items = b.items ?? [];
  const cls = "my-1 pl-5 text-sm space-y-0.5";
  if (b.ordered) {
    return <ol className={`${cls} list-decimal`}>{items.map((it, i) => <li key={i}>{it}</li>)}</ol>;
  }
  return <ul className={`${cls} list-disc`}>{items.map((it, i) => <li key={i}>{it}</li>)}</ul>;
}

function renderChange(b: Block): ReactNode {
  const isDelete = b.change_type === "deletion" || b.change_type === "delete";
  const cls = isDelete
    ? "bg-red-50 text-red-700 line-through px-1 rounded"
    : "bg-green-50 text-green-800 px-1 rounded";
  return (
    <span className={`text-sm ${cls}`}>
      {b.text}
      {b.author && <em className="ml-1 text-[10px] opacity-70 not-italic">— {b.author}</em>}
    </span>
  );
}

function renderImage(b: Block): ReactNode {
  const label = b.description || b.mime || "embedded";
  return (
    <div className="my-2 px-3 py-2 bg-muted/30 rounded text-xs italic text-muted-foreground">
      [Image: {label}]
    </div>
  );
}

function renderBlock(b: Block, key: number): ReactNode {
  switch (b.type) {
    case "heading":
      return <div key={key}>{renderHeading(b.level ?? 1, b.text ?? "")}</div>;
    case "text":
      return <div key={key}>{renderText(b)}</div>;
    case "table":
      return <div key={key}>{renderTable(b)}</div>;
    case "list":
      return <div key={key}>{renderList(b)}</div>;
    case "change":
      return <div key={key} className="my-1">{renderChange(b)}</div>;
    case "image":
      return <div key={key}>{renderImage(b)}</div>;
    case "section":
      return (
        <section key={key} className="my-2">
          {b.kind && <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">{b.kind}</div>}
          {(b.children ?? []).map((c, i) => renderBlock(c, i))}
        </section>
      );
    default:
      if (b.children?.length) {
        return <div key={key}>{b.children.map((c, i) => renderBlock(c, i))}</div>;
      }
      if (b.text) return <p key={key} className="my-1 text-sm">{b.text}</p>;
      return null;
  }
}

interface BlocksRendererProps {
  blocks: Block[];
}

export function BlocksRenderer({ blocks }: BlocksRendererProps) {
  return (
    <div className="space-y-1">
      {blocks.map((b, i) => renderBlock(b, i))}
    </div>
  );
}
