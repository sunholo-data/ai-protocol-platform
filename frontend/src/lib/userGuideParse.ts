/**
 * User guide page structure — pure functions shared by /help and its tests.
 *
 * A page (docs/user-guide/<slug>.md, bundled by scripts/user-guide/bundle.mjs)
 * is split at its `##`/`###` headings. Each section becomes a list of segments:
 * markdown text, GitHub-style alerts (`> [!NOTE]`), and — when the section ENDS
 * in images — a figure group shown beside the section's steps. Layout comes from
 * that structure, so authors write plain Markdown and never layout markup.
 */

export type Audience = "user" | "owner" | "admin";

export interface GuideSection {
  level: number;
  title: string;
  anchor: string;
}

export interface GuidePageMeta {
  slug: string;
  title: string;
  summary: string;
  audience: Audience;
  order: number;
  requires: string[];
  layer: "platform" | "customer";
  sections: GuideSection[];
}

export interface GuideManifest {
  tags: string[];
  pages: GuidePageMeta[];
}

export interface Figure {
  alt: string;
  src: string;
  caption: string;
}

export type Segment =
  | { kind: "markdown"; text: string }
  | { kind: "alert"; type: AlertType; text: string }
  | { kind: "figures"; figures: Figure[] };

export type AlertType = "note" | "tip" | "important" | "warning" | "caution";

export interface ParsedSection {
  /** null for the text before the first heading. */
  heading: GuideSection | null;
  /** Segments in reading order, WITHOUT the trailing figures. */
  segments: Segment[];
  /** Images the section ends with: rendered beside `segments`. */
  aside: Figure[];
}

/** GitHub-style heading anchor. Same function as scripts/user-guide/bundle.mjs — both are tested on the same cases. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[*_`]/g, "")
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .trim()
    .replace(/\s/g, "-");
}

const IMAGE_LINE = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)\s*$/;
const HEADING = /^(#{2,3})\s+(.+?)\s*#*$/;
const ALERT_START = /^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$/i;

/** Where a bundled image is served from (the bundle copies img/** to public/help/img/**). */
export function imageUrl(src: string): string {
  if (/^[a-z]+:/i.test(src) || src.startsWith("/")) return src;
  return `/help/${src.replace(/^\.\//, "")}`;
}

/** Links between pages: `other.md#x` → `/help/other#x`. Anchors and absolute links are left alone. */
export function linkUrl(href: string): string {
  const m = /^([\w-]+)\.md(#.*)?$/.exec(href);
  return m ? `/help/${m[1]}${m[2] ?? ""}` : href;
}

export function withAppName(text: string, appName: string): string {
  return text.replace(/\{\{\s*appName\s*\}\}/g, appName);
}

/** Split a page body into sections. Headings inside code fences are not headings. */
export function parsePage(body: string): ParsedSection[] {
  const lines = body.split("\n");
  const raw: { heading: GuideSection | null; lines: string[] }[] = [{ heading: null, lines: [] }];
  const seen = new Map<string, number>();
  let fenced = false;
  for (const line of lines) {
    if (/^```/.test(line)) fenced = !fenced;
    const h = !fenced ? HEADING.exec(line) : null;
    if (h) {
      const base = slugify(h[2]);
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      raw.push({ heading: { level: h[1].length, title: h[2], anchor: n ? `${base}-${n}` : base }, lines: [] });
    } else {
      raw[raw.length - 1].lines.push(line);
    }
  }
  return raw
    .filter((s, i) => i > 0 || s.lines.some((l) => l.trim()))
    .map(({ heading, lines: ls }) => {
      // Trailing images (blank lines between them allowed) become the aside.
      let end = ls.length;
      while (end > 0 && !ls[end - 1].trim()) end -= 1;
      const aside: Figure[] = [];
      let cut = end;
      while (cut > 0) {
        const l = ls[cut - 1];
        if (!l.trim()) {
          cut -= 1;
          continue;
        }
        const m = IMAGE_LINE.exec(l);
        if (!m) break;
        aside.unshift({ alt: m[1], src: imageUrl(m[2]), caption: m[3] ?? "" });
        cut -= 1;
      }
      return { heading, segments: toSegments(ls.slice(0, cut)), aside };
    });
}

function toSegments(lines: string[]): Segment[] {
  const out: Segment[] = [];
  let buf: string[] = [];
  const flush = () => {
    const text = buf.join("\n").trim();
    if (text) out.push({ kind: "markdown", text });
    buf = [];
  };
  let fenced = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) fenced = !fenced;
    const alert = !fenced ? ALERT_START.exec(line) : null;
    if (alert) {
      flush();
      const inner: string[] = [];
      while (i + 1 < lines.length && /^>/.test(lines[i + 1])) {
        i += 1;
        inner.push(lines[i].replace(/^>\s?/, ""));
      }
      out.push({ kind: "alert", type: alert[1].toLowerCase() as AlertType, text: inner.join("\n").trim() });
      continue;
    }
    // Consecutive image lines in the middle of a section stay figures, not inline <img>.
    const img = !fenced ? IMAGE_LINE.exec(line) : null;
    if (img) {
      flush();
      const last = out[out.length - 1];
      const fig = { alt: img[1], src: imageUrl(img[2]), caption: img[3] ?? "" };
      if (last?.kind === "figures") last.figures.push(fig);
      else out.push({ kind: "figures", figures: [fig] });
      continue;
    }
    buf.push(line);
  }
  flush();
  return out;
}

/** Pages a viewer should see listed: their audience, and features that are on. */
export function visiblePages(pages: GuidePageMeta[], opts: { isAdmin?: boolean; features?: Set<string> } = {}): GuidePageMeta[] {
  return pages.filter(
    (p) =>
      (p.audience !== "admin" || opts.isAdmin) &&
      (p.requires.length === 0 || !opts.features || p.requires.every((f) => opts.features?.has(f))),
  );
}
