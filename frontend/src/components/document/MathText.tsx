"use client";

// v6.34.0 DOC-WORKBENCH — render formulae in the Text view.
//
// The block view shows the parsed text verbatim, so a document containing maths
// displayed its raw source — `$E = mc^2$` or `\frac{a}{b}` as literal
// characters. Reported on test 2026-09-02.
//
// KaTeX renders the segments between `$…$` (inline) and `$$…$$` (display) and
// leaves everything else exactly as it was. Deliberately conservative: text that
// merely CONTAINS a dollar sign — a price, which a commercial document is full
// of — must not be mistaken for maths, so a delimiter only counts when it pairs
// and the content between looks like an expression rather than a number.

import { useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";

/** `$$…$$` first (display), then `$…$` (inline). */
const MATH_PATTERN = /\$\$([^$]+?)\$\$|\$([^$\n]+?)\$/g;

/**
 * True when the delimited content is plausibly maths rather than a price range.
 *
 * "$1.2M" and "costs $5 to $10" are the false positives that matter here — a
 * commercial document has far more prices in it than equations.
 */
function looksLikeMath(body: string): boolean {
  const trimmed = body.trim();
  if (!trimmed) return false;
  // Pure numbers/currency — "5", "1.2M", "10,000" — are not formulae.
  if (/^[\d.,\s]+[A-Za-z]{0,2}$/.test(trimmed)) return false;
  // A TeX command, or an operator/structure that prose would not contain.
  return /\\[a-zA-Z]+|[=^_]|\\frac|\\sum|\\int/.test(trimmed);
}

interface MathTextProps {
  text: string;
}

/** Renders text, turning `$…$` / `$$…$$` into typeset maths. */
export function MathText({ text }: MathTextProps) {
  const parts = useMemo(() => {
    if (!text || !text.includes("$")) return null;

    const out: Array<{ kind: "text" | "math"; value: string; display?: boolean }> = [];
    let last = 0;
    for (const m of text.matchAll(MATH_PATTERN)) {
      const body = m[1] ?? m[2] ?? "";
      const display = m[1] !== undefined;
      if (!looksLikeMath(body)) continue;
      if (m.index! > last) out.push({ kind: "text", value: text.slice(last, m.index) });
      out.push({ kind: "math", value: body, display });
      last = m.index! + m[0].length;
    }
    if (out.length === 0) return null;
    if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
    return out;
  }, [text]);

  if (!parts) return <>{text}</>;

  return (
    <>
      {parts.map((p, i) => {
        if (p.kind === "text") return <span key={i}>{p.value}</span>;
        let html: string;
        try {
          html = katex.renderToString(p.value, { displayMode: p.display, throwOnError: false });
        } catch {
          // A formula KaTeX cannot parse stays as its source rather than
          // vanishing — unreadable maths beats absent maths.
          return <span key={i}>{p.display ? `$$${p.value}$$` : `$${p.value}$`}</span>;
        }
        return <span key={i} dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </>
  );
}
