"use client";

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { InlineCitation } from "@/components/chat/InlineCitation";
import { CITATION_SCHEME } from "@/lib/branding";
import { CodeBlock, hastLanguage, hastText } from "@/components/chat/CodeBlock";
import { SVGBlock } from "@/components/chat/media/SVGBlock";
import { InlineImage } from "@/components/chat/media/InlineImage";
import { PDFCard } from "@/components/chat/media/PDFCard";
import type { Components } from "react-markdown";

interface ChatMarkdownProps {
  content: string;
  navigateToBlock: (docId: string, blockId: string) => void;
}

// Unique sentinel that won't appear in real markdown content.
// Pre-processing extracts ```svg blocks before react-markdown/rehypeHighlight
// sees them, because rehypeHighlight recognises 'svg' as an alias for its XML
// highlighter and transforms the text content into React span elements — making
// String(children) useless inside the code renderer. By replacing svg fences
// with this sentinel we bypass the pipeline and render SVGBlock directly.
//
// IMPORTANT: No leading/trailing underscores or asterisks — GFM would parse
// __text__ or **text** as bold, breaking the sentinel detection in the p renderer.
const SVG_SENTINEL_PREFIX = "AITANASVGBLOCK";
const SVG_SENTINEL_SUFFIX = "END";
const SVG_FENCE_RE = /```svg\r?\n([\s\S]*?)```/g;

export function ChatMarkdown({ content, navigateToBlock }: ChatMarkdownProps) {
  // Extract ```svg blocks before react-markdown processes them.
  // Returns a cleaned content string plus a map of index → raw SVG string.
  const { processedContent, svgBlocks } = useMemo(() => {
    const blocks = new Map<number, string>();
    let idx = 0;
    const processed = content.replace(SVG_FENCE_RE, (_match, svgCode: string) => {
      blocks.set(idx, svgCode.trim());
      return `${SVG_SENTINEL_PREFIX}${idx++}${SVG_SENTINEL_SUFFIX}`;
    });
    return { processedContent: processed, svgBlocks: blocks };
  }, [content]);

  // MEMOISED, and that is load-bearing (v6.19.0, AIPLA #44).
  //
  // react-markdown treats each entry in `components` as a React element TYPE.
  // A fresh object identity per render therefore makes React REMOUNT the whole
  // rendered subtree rather than re-render it — every message's DOM torn down
  // and rebuilt on any parent re-render. It surfaced as continuous SVG-diagram
  // flicker, but the remount cost is generic to every chat message.
  //
  // Deps are exactly what the closures below read: `navigateToBlock` (used by
  // InlineCitation) and `svgBlocks` (used by the code renderer). `svgBlocks` is
  // itself memoised on `content` above, so this is stable across unrelated
  // parent renders.
  const components: Components = useMemo(() => ({
    a({ href, children }) {
      const h = href ?? "#";
      // Citation-scheme links → InlineCitation chip
      if (h.startsWith(`${CITATION_SCHEME}://`)) {
        return (
          <InlineCitation href={h} navigateToBlock={navigateToBlock}>
            {children}
          </InlineCitation>
        );
      }
      // PDF links → card with filename + page count
      const safePdf =
        h.startsWith("https://") || h.startsWith("http://") ? h : null;
      if (safePdf && safePdf.toLowerCase().endsWith(".pdf")) {
        return <PDFCard url={safePdf} />;
      }
      // External https/http/mailto links → plain anchor
      const safe =
        h.startsWith("https://") || h.startsWith("http://") || h.startsWith("mailto:")
          ? h
          : "#";
      return (
        <a href={safe} target="_blank" rel="noopener noreferrer" className="text-teal-600 underline">
          {children}
        </a>
      );
    },
    img({ src, alt }) {
      if (!src || typeof src !== "string") return null;
      return <InlineImage src={src} alt={alt} />;
    },
    // Strip raw HTML passthrough — prevents XSS from agent output
    html() {
      return null;
    },
    p({ children }) {
      // Detect SVG block sentinels injected by pre-processing above
      const first = Array.isArray(children) ? children[0] : children;
      if (typeof first === "string") {
        const match = first.match(new RegExp(`^${SVG_SENTINEL_PREFIX}(\\d+)${SVG_SENTINEL_SUFFIX}$`));
        if (match) {
          const svgString = svgBlocks.get(parseInt(match[1]));
          if (svgString) return <SVGBlock svgString={svgString} />;
        }
      }
      return <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>;
    },
    h1({ children }) {
      return <h1 className="mb-2 text-base font-semibold">{children}</h1>;
    },
    h2({ children }) {
      return <h2 className="mb-2 text-sm font-semibold">{children}</h2>;
    },
    h3({ children }) {
      return <h3 className="mb-1 text-sm font-medium">{children}</h3>;
    },
    ul({ children }) {
      return <ul className="mb-2 list-disc pl-4 space-y-0.5">{children}</ul>;
    },
    ol({ children }) {
      return <ol className="mb-2 list-decimal pl-4 space-y-0.5">{children}</ol>;
    },
    li({ children }) {
      return <li className="text-sm">{children}</li>;
    },
    strong({ children }) {
      return <strong className="font-semibold">{children}</strong>;
    },
    em({ children }) {
      return <em className="italic">{children}</em>;
    },
    code({ className, children, ...props }) {
      // rehypeHighlight may prepend 'hljs' class: "hljs language-xml" — check all tokens.
      const classes = className?.split(/\s+/) ?? [];
      const langClass = classes.find((c) => c.startsWith("language-"));
      const isBlock = !!langClass;

      if (isBlock) {
        return (
          <code className={`${className ?? ""} text-xs`} {...props}>
            {children}
          </code>
        );
      }
      return (
        <code className="rounded bg-muted px-1 py-0.5 text-xs font-mono" {...props}>
          {children}
        </code>
      );
    },
    pre({ children, node }) {
      // Text is read from the ORIGINAL hast node, not `children` — by now
      // rehypeHighlight has replaced the text with <span> elements. See the
      // note at the top of CodeBlock.tsx.
      return (
        <CodeBlock text={hastText(node)} language={hastLanguage(node)}>
          {children}
        </CodeBlock>
      );
    },
    table({ children }) {
      return (
        <div className="mb-2 overflow-x-auto">
          <table className="w-full text-xs border-collapse">{children}</table>
        </div>
      );
    },
    th({ children }) {
      return (
        <th className="border border-border bg-muted px-2 py-1 text-left font-medium">
          {children}
        </th>
      );
    },
    td({ children }) {
      return <td className="border border-border px-2 py-1">{children}</td>;
    },
    blockquote({ children }) {
      return (
        <blockquote className="mb-2 border-l-2 border-muted-foreground/40 pl-3 text-muted-foreground">
          {children}
        </blockquote>
      );
    },
  }), [navigateToBlock, svgBlocks]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeHighlight, { ignoreMissing: true }]]}
      components={components}
      urlTransform={(url) => {
        // Allow citation-scheme, https://, http://, mailto: — block everything else
        if (
          url.startsWith(`${CITATION_SCHEME}://`) ||
          url.startsWith("https://") ||
          url.startsWith("http://") ||
          url.startsWith("mailto:")
        ) {
          return url;
        }
        return "#";
      }}
    >
      {processedContent}
    </ReactMarkdown>
  );
}
