/**
 * Renders one user guide page (see src/lib/userGuideParse.ts for the structure).
 *
 * A section that ends in screenshots shows them beside its steps, in the
 * same layout as the hand-made guides it replaces. Styling is explicit
 * (this app has no typography plugin) and uses the theme tokens, so forks that
 * restyle the tokens restyle the guide.
 */

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CircleHelp, Info, Lightbulb, TriangleAlert } from "lucide-react";

import { linkUrl, parsePage, withAppName, type AlertType, type Figure, type ParsedSection, type Segment } from "@/lib/userGuideParse";

/**
 * Called with a link's resolved href (`/help/other#x`) before the browser
 * follows it; return true to handle it in place (the workbench Help tab does).
 */
export type GuideLinkHandler = (href: string) => boolean;

interface RenderOpts {
  appName: string;
  onLink?: GuideLinkHandler;
  /** Narrow container (the workbench): screenshots go below the steps, not beside them. */
  compact?: boolean;
}

const md: Components = {
  p: ({ children }) => <p className="leading-relaxed">{children}</p>,
  ol: ({ children }) => <ol className="list-decimal space-y-1.5 pl-6">{children}</ol>,
  ul: ({ children }) => <ul className="list-disc space-y-1.5 pl-6">{children}</ul>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  a: ({ href, children }) => (
    <a href={linkUrl(href ?? "")} className="text-primary underline underline-offset-2 hover:no-underline">
      {children}
    </a>
  ),
  code: ({ className, children }) =>
    className ? (
      <code className={className}>{children}</code>
    ) : (
      <code className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
    ),
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-[var(--radius)] border border-border bg-muted p-3 font-mono text-[13px] leading-relaxed">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-border px-2.5 py-2 text-left font-mono text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="border-b border-border px-2.5 py-2 align-top">{children}</td>,
};

const ALERTS: Record<AlertType, { label: string; icon: typeof Info; tone: string }> = {
  note: { label: "Note", icon: Info, tone: "border-sky-500/40 bg-sky-500/5" },
  tip: { label: "Tip", icon: Lightbulb, tone: "border-emerald-500/40 bg-emerald-500/5" },
  important: { label: "Important", icon: CircleHelp, tone: "border-violet-500/40 bg-violet-500/5" },
  warning: { label: "Warning", icon: TriangleAlert, tone: "border-amber-500/50 bg-amber-500/10" },
  caution: { label: "Caution", icon: TriangleAlert, tone: "border-red-500/50 bg-red-500/10" },
};

function GuideLink({ href, onLink, children }: { href: string; onLink?: GuideLinkHandler; children: ReactNode }) {
  const url = linkUrl(href);
  return (
    <a
      href={url}
      onClick={onLink ? (e) => { if (onLink(url)) e.preventDefault(); } : undefined}
      className="text-primary underline underline-offset-2 hover:no-underline"
    >
      {children}
    </a>
  );
}

function Markdown({ text, appName, onLink }: { text: string } & RenderOpts) {
  const components = onLink
    ? { ...md, a: ({ href, children }: { href?: string; children?: ReactNode }) => <GuideLink href={href ?? ""} onLink={onLink}>{children}</GuideLink> }
    : md;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {withAppName(text, appName)}
    </ReactMarkdown>
  );
}

function Figures({ figures, appName, compact }: { figures: Figure[]; appName: string; compact?: boolean }) {
  return (
    <div className={figures.length > 1 ? "grid gap-3 sm:grid-cols-2" : ""}>
      {figures.map((f) => (
        <figure key={f.src} className="space-y-1.5">
          {/* Screenshots are static assets from the bundle; next/image adds nothing here. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={f.src}
            alt={withAppName(f.alt, appName)}
            loading="lazy"
            className={
              compact
                ? // Narrow pane: a tight crop must not blow up to the pane's width.
                  "block h-auto max-h-[420px] w-auto max-w-full rounded-lg border border-border bg-white shadow-sm"
                : "block h-auto w-full rounded-lg border border-border bg-white shadow-sm"
            }
          />
          {f.caption ? <figcaption className="text-xs text-muted-foreground">{withAppName(f.caption, appName)}</figcaption> : null}
        </figure>
      ))}
    </div>
  );
}

function SegmentView({ seg, ...opts }: { seg: Segment } & RenderOpts) {
  const { appName } = opts;
  if (seg.kind === "markdown") return <Markdown text={seg.text} {...opts} />;
  if (seg.kind === "figures") return <Figures figures={seg.figures} appName={appName} compact={opts.compact} />;
  const a = ALERTS[seg.type];
  const Icon = a.icon;
  return (
    <div role="note" aria-label={a.label} className={`flex gap-2.5 rounded-[var(--radius)] border px-3.5 py-2.5 text-[0.95em] ${a.tone}`}>
      <Icon className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 space-y-2">
        <Markdown text={seg.text} {...opts} />
      </div>
    </div>
  );
}

function SectionView({ section, ...opts }: { section: ParsedSection } & RenderOpts) {
  const { appName, compact } = opts;
  const { heading, segments, aside } = section;
  const Tag = heading?.level === 3 ? "h3" : "h2";
  const body = (
    <div className="min-w-0 space-y-3">
      {segments.map((seg, i) => (
        <SegmentView key={i} seg={seg} {...opts} />
      ))}
    </div>
  );
  return (
    <section className={heading?.level === 2 ? "space-y-4 border-t border-border pt-8" : "space-y-3 pt-2"}>
      {heading ? (
        <Tag id={heading.anchor} className={`scroll-mt-20 font-semibold tracking-tight text-foreground ${Tag === "h2" ? "text-xl" : "text-base"}`}>
          <a href={`#${heading.anchor}`} className="hover:underline">
            {withAppName(heading.title, appName)}
          </a>
        </Tag>
      ) : null}
      {aside.length && (!heading || compact) ? (
        // The page intro's screenshot is the overview shot: full width, like a hero.
        <>
          {body}
          <Figures figures={aside} appName={appName} compact={compact} />
        </>
      ) : aside.length ? (
        <div className="grid items-start gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,300px)]">
          {body}
          <Figures figures={aside} appName={appName} compact={compact} />
        </div>
      ) : (
        body
      )}
    </section>
  );
}

export function GuideBody({ body, ...opts }: { body: string } & RenderOpts) {
  const sections = parsePage(body);
  return (
    <div className="space-y-6 text-[15px] text-foreground/90" data-testid="guide-body">
      {sections.map((s, i) => (
        <SectionView key={s.heading?.anchor ?? `intro-${i}`} section={s} {...opts} />
      ))}
    </div>
  );
}
