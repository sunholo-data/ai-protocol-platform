"use client";

import { useRef } from "react";
import { Catalog } from "@a2ui/web_core/v0_9";
import { TextApi, ModalApi } from "@a2ui/web_core/v0_9/basic_catalog";
import { basicCatalog, createComponentImplementation } from "@a2ui/react/v0_9";
import * as Dialog from "@radix-ui/react-dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MarkdownText } from "./a2uiMarkdownCatalog";

// Explicit body Text is rich document content. Every other variant goes through
// the markdown Text (a2uiMarkdownCatalog): headings stay plain, prose renders
// markdown, because agents write markdown. No tool-specific renderer or extra
// wire schema.
const RichText = createComponentImplementation(TextApi, ({ props, context, buildChild }) => {
  if (props.variant !== "body") {
    const Prose = MarkdownText.render;
    return <Prose context={context} buildChild={buildChild} />;
  }
  return (
    <div className="a2ui-document">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
        // Inbox documents are untrusted. Display alt text without requesting
        // remote images or embedding active HTML/SVG content.
        img: ({ alt }) => <span>{alt || "[image]"}</span>,
      }}>{props.text ?? ""}</ReactMarkdown>
    </div>
  );
});

// The stock modal has no keyboard dismissal/focus trap and a hardcoded white
// background. Keep the Basic API but use the platform's accessible dialog.
const AccessibleModal = createComponentImplementation(ModalApi, ({ props, buildChild }) => {
  const trigger = useRef<HTMLElement | null>(null);
  return (
  <Dialog.Root>
    <Dialog.Trigger asChild><span onClickCapture={event => { trigger.current = (event.target as HTMLElement).closest("button") ?? event.currentTarget; }}>{props.trigger ? buildChild(props.trigger) : null}</span></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
      <Dialog.Content onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }} aria-describedby={undefined} className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[min(48rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-border bg-background p-6 shadow-xl">
        <Dialog.Title className="sr-only">Details</Dialog.Title>
        <Dialog.Close className="absolute right-3 top-2 rounded px-2 py-1 text-muted-foreground hover:bg-muted" aria-label="Close details">×</Dialog.Close>
        <div data-surface="document" className="pr-3">{props.content ? buildChild(props.content) : null}</div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
);
});

export const workbenchCatalog = new Catalog(
  basicCatalog.id,
  [...basicCatalog.components.values()].map(component =>
    component.name === "Text" ? RichText : component.name === "Modal" ? AccessibleModal : component),
  [...basicCatalog.functions.values()],
  basicCatalog.themeSchema,
);
