"use client";

import { useState, useEffect } from "react";
import * as Dialog from "@radix-ui/react-dialog";

// Strip scripts, external references, and event handlers from agent-generated SVG.
// Config is a named constant so security audits can find and review it in one place.
const PURIFY_CONFIG = {
  USE_PROFILES: { svg: true, svgFilters: true },
  FORBID_TAGS: ["script", "use"],
  // Prevent SSRF via SVG external references
  FORBID_ATTR: ["xlink:href", "href"],
};

interface SVGBlockProps {
  svgString: string;
}

// Make an agent-authored SVG scale to its container instead of rendering at its
// baked-in pixel size.
//
// The model emits diagrams with fixed `width`/`height` (e.g. width="300"), so
// they render tiny in a wide chat column and asking the model to "make it
// larger" just yields another fixed-size SVG — the size lives in the markup, not
// in anything we can prompt for. An SVG scales to CSS only when it has a
// `viewBox` and no competing intrinsic width/height, so we:
//   1. synthesise a viewBox from width/height when the model omitted one, then
//   2. drop the fixed width/height so CSS (width:100%) governs the size.
// If there's neither a viewBox nor usable dimensions we leave it untouched —
// forcing width:100% on such an SVG would collapse its height to zero.
//
// Runs on the ALREADY-sanitised string and only touches width/height/viewBox on
// the root element, so it cannot reintroduce anything DOMPurify removed.
function makeResponsive(cleanSvg: string): string {
  try {
    const doc = new DOMParser().parseFromString(cleanSvg, "image/svg+xml");
    const el = doc.documentElement;
    if (el.nodeName.toLowerCase() !== "svg" || doc.querySelector("parsererror")) return cleanSvg;

    if (!el.getAttribute("viewBox")) {
      const w = parseFloat(el.getAttribute("width") ?? "");
      const h = parseFloat(el.getAttribute("height") ?? "");
      if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
        el.setAttribute("viewBox", `0 0 ${w} ${h}`);
      }
    }
    if (el.getAttribute("viewBox")) {
      el.removeAttribute("width");
      el.removeAttribute("height");
      return new XMLSerializer().serializeToString(el);
    }
    return cleanSvg;
  } catch {
    return cleanSvg;
  }
}

interface CodeFallbackProps {
  code: string;
}

function CodeFallback({ code }: CodeFallbackProps) {
  return (
    <pre className="mb-2 overflow-x-auto rounded border border-border bg-muted p-3 text-xs">
      <code>{code}</code>
    </pre>
  );
}

// Shown while DOMPurify is dynamically imported and the SVG is sanitised
// (a client-only async step). Reserving space here avoids the raw-code →
// blank → pop-in layout shift and gives the user a visible "working" state
// instead of nothing (never-silent principle). Initial server + client
// render both hit this branch (cleanSvg is "" until the effect runs), so
// there is no hydration mismatch.
function SVGPlaceholder() {
  return (
    <div
      className="svg-placeholder my-4 flex min-h-24 items-center justify-center rounded border border-border bg-muted/40 p-2"
      aria-busy="true"
      aria-label="Rendering diagram"
    >
      <span className="animate-pulse text-xs text-muted-foreground">Rendering diagram…</span>
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="15 3 21 3 21 9" />
      <polyline points="9 21 3 21 3 15" />
      <line x1="21" y1="3" x2="14" y2="10" />
      <line x1="3" y1="21" x2="10" y2="14" />
    </svg>
  );
}

export function SVGBlock({ svgString }: SVGBlockProps) {
  // Empty string initial state: server renders nothing (no hydration mismatch).
  // useEffect + dynamic import: DOMPurify only runs in the browser where DOM is available.
  const [cleanSvg, setCleanSvg] = useState("");
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    import("dompurify").then(({ default: DOMPurify }) => {
      const clean = DOMPurify.sanitize(svgString, PURIFY_CONFIG) as string;
      if (!clean) {
        setFailed(true);
      } else {
        // Scale-to-container happens AFTER sanitise — never before, and never on
        // the raw string — so the security pass is always what runs first.
        setCleanSvg(makeResponsive(clean));
      }
    });
  }, [svgString]);

  if (failed) return <CodeFallback code={svgString} />;
  if (!cleanSvg) return <SVGPlaceholder />;

  // The inline diagram doubles as the trigger (mirrors InlineImage's
  // cursor-zoom-in). A diagram is often dense and small in the chat column, so
  // a full-screen view is the difference between "I can see there's a chart"
  // and "I can read it". The modal re-renders the SAME sanitised string — the
  // security work already happened; do not re-sanitise, and never render the
  // raw svgString here.
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <div className="group relative my-4">
        <Dialog.Trigger asChild>
          <button
            type="button"
            aria-label="Expand diagram to full screen"
            // The child-selectors are what make it "larger": once makeResponsive
            // has ensured a viewBox and dropped the fixed dimensions, the inner
            // <svg> fills the column width (w-full) instead of its baked-in size.
            // max-h caps a tall/square diagram so it doesn't dominate the
            // transcript — full-screen is there for the rest.
            className="svg-container block w-full max-w-full cursor-zoom-in overflow-x-auto rounded border border-border p-2 text-left transition-colors hover:border-primary/50 [&>svg]:h-auto [&>svg]:max-h-[60vh] [&>svg]:w-full"
            dangerouslySetInnerHTML={{ __html: cleanSvg }}
          />
        </Dialog.Trigger>
        {/* Explicit affordance — a bare SVG doesn't read as clickable. Visible
            on hover/focus-within so it doesn't clutter the transcript. */}
        <Dialog.Trigger asChild>
          {/* Visible "Expand" text is this button's accessible name — no
              aria-label, so it doesn't duplicate the SVG trigger's label. */}
          <button
            type="button"
            className="absolute right-2 top-2 flex items-center gap-1 rounded-md border border-border bg-background/90 px-2 py-1 text-xs text-muted-foreground opacity-0 shadow-sm transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
          >
            <ExpandIcon />
            Expand
          </button>
        </Dialog.Trigger>
      </div>

      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/70" />
        <Dialog.Content
          // DEFINITE size (w-/h-, not max-w-/max-h-) is load-bearing: with only
          // max-* the flex-col content shrink-wraps to the SVG's intrinsic size,
          // so the inner `w-full` resolves against an auto width and collapses
          // back to the baked-in size — the expanded diagram came out the SAME
          // or smaller than the inline one. A definite 92vw×92vh box gives the
          // SVG something real to fill.
          className="fixed left-1/2 top-1/2 z-50 flex h-[92vh] w-[92vw] -translate-x-1/2 -translate-y-1/2 flex-col outline-none"
          aria-describedby={undefined}
        >
          <Dialog.Title className="sr-only">Diagram — full screen</Dialog.Title>
          {/* `flex-1 min-h-0` makes this pane take the modal's full height; the
              inner <svg> then fills it via w-full/h-full and scales UP to the
              viewport (a 300px diagram becomes full-screen). The SVG's default
              preserveAspectRatio="xMidYMid meet" keeps the aspect ratio and
              letterboxes, so filling both axes never distorts. */}
          <div
            className="flex min-h-0 flex-1 items-center justify-center overflow-auto rounded-lg bg-background p-4 shadow-2xl [&_svg]:h-full [&_svg]:w-full"
            dangerouslySetInnerHTML={{ __html: cleanSvg }}
          />
          <Dialog.Close
            className="absolute -right-3 -top-3 flex h-7 w-7 items-center justify-center rounded-full bg-white text-black shadow-md hover:bg-muted"
            aria-label="Close full-screen diagram"
          >
            ×
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
