// The Basic catalog, with a `Text` that renders markdown.
//
// WHY THIS EXISTS
//
// Agents write markdown. It is what a model emits when it means "here is a
// command" or "here are three options", and the A2UI Basic catalog's `Text`
// renders `props.text` as a plain string — so a resident agent's answer arrived
// in the workbench with its backticks and asterisks showing (reported from the
// browser, 2026-09-06). Every surface that carries model-authored prose has the
// same problem; the resident story is just where it was noticed.
//
// WHY A CATALOG AND NOT A COMPONENT
//
// CLAUDE.md #7: no bespoke React per tool or per skill. This does not add a
// component or key anything off a tool name — it swaps ONE implementation
// inside the catalog every surface already renders through, so the fix lands
// everywhere at once and nothing upstream has to know. The catalog keeps
// `basicCatalog.id`, so the wire format is unchanged and a server that sends
// `catalogId: basic_catalog.json` still matches.
//
// HEADINGS STAY PLAIN, ON PURPOSE
//
// `variant: h1..h4` marks a short label — a section title, a question — not
// prose. Running those through a block-level markdown renderer would wrap them
// in their own <p> and lose the heading semantics for no gain. Markdown is for
// the body, which is where models actually write it.

"use client";

import {
  Text as BasicText,
  basicCatalog,
  createComponentImplementation,
} from "@a2ui/react/v0_9";
import { Catalog } from "@a2ui/web_core/v0_9";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

const HEADING_VARIANTS = new Set(["h1", "h2", "h3", "h4"]);

/**
 * Deliberately NOT `ChatMarkdown`.
 *
 * That component is the chat surface's: it resolves doc-block links, renders
 * PDF cards and pulls in an SVG pipeline, and it REQUIRES a `navigateToBlock`
 * callback that a workbench surface has no meaning for. Sharing it would drag
 * chat concerns into every A2UI surface to gain a renderer this does in ten
 * lines. Same library, same GFM, no coupling.
 */
function Prose({ text }: { text: string }) {
  return (
    <div className="a2ui-md">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          // Peer-controlled text renders here — an agent's answer, a tool's
          // output. Links open in a new tab and carry noopener, because a
          // surface must not be able to navigate the app it is embedded in.
          a: ({ ...p }) => <a {...p} target="_blank" rel="noopener noreferrer" />,
          // Same rule as workbenchCatalog's document Text: a remote image in
          // peer-controlled text is a request we did not choose to make (and
          // an exfiltration channel), so show its alt text instead.
          img: ({ alt }) => <span>{alt || "[image]"}</span>,
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}

/**
 * The whole of the decision, as a plain component.
 *
 * Split out from the catalog entry because a `ReactComponentImplementation`
 * cannot be rendered on its own — it wants the SDK's binder and component
 * context — so testing the BEHAVIOUR through it would mean reconstructing half
 * the runtime. This is the part with the judgement in it; the catalog entry
 * below is the wiring, and the composition tests cover that.
 */
export function TextContent({ text, variant }: { text: string; variant?: string }) {
  if (variant && HEADING_VARIANTS.has(variant)) {
    const Tag = variant as "h1" | "h2" | "h3" | "h4";
    return <Tag style={{ display: "inline-block" }}>{text}</Tag>;
  }
  // An empty string is a real value — a tool that returned nothing — and must
  // not collapse the row it was meant to occupy.
  if (!text) return <span />;
  return <Prose text={text} />;
}

export const MarkdownText = createComponentImplementation(BasicText, ({ props }) => (
  <TextContent
    text={String((props as { text?: unknown }).text ?? "")}
    variant={(props as { variant?: string }).variant}
  />
));

/**
 * Basic, with `Text` replaced. Same id, so the wire is unchanged.
 *
 * `components` is an array in the constructor while `basicCatalog.components`
 * is a Map keyed by name — hence the values() spread rather than an object
 * merge. Functions and themeSchema are carried across untouched; dropping them
 * would silently disable expression bindings, which fail as an unresolved
 * `{"path": …}` rather than an error.
 */
export const markdownCatalog = new Catalog(
  basicCatalog.id,
  [...basicCatalog.components.values()].map((c) =>
    c.name === "Text" ? MarkdownText : c,
  ),
  [...basicCatalog.functions.values()],
  basicCatalog.themeSchema,
);
