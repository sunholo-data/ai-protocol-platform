"use client";

import type { ReactNode } from "react";
import type { ExampleDocument, ExamplePrompt } from "@/types/skill";
import type { RecentDocument, RecentDocumentsStatus } from "@/hooks/useRecentDocuments";
import { cn } from "@/lib/utils";
import { relativeTime } from "@/lib/relativeTime";
import { DocumentThumbnail } from "@/components/document/DocumentThumbnail";

interface SkillExamplesPickerProps {
  examples: ExampleDocument[];
  /** Called when the user clicks a card. Parent decides what to do — typically
   * fires a synthetic chat message asking the agent to load the example via
   * its existing bucket tools (list_documents / get_document_content). */
  onPickExample: (example: ExampleDocument) => void;
  /** v6.12.0 first-look ACTION cards — `welcome.examplePrompts`. Shows the
   * skill's real range (market data, comparison, analysis, research) instead of
   * only "import a document". Optional: omit and the picker is doc-only. */
  prompts?: ExamplePrompt[];
  /** Send a prompt card's text as a normal chat message (the demo path IS the
   * product path). Required for prompt cards to render. */
  onPickPrompt?: (prompt: string) => void;
  /** Click handler for the "Or upload your own" secondary link. Parent opens
   * the existing UploadDropZone or scrolls to it. */
  onUploadOwn?: () => void;
  /**
   * Layout context. `panel` (default) fills a bounded container — the Workbench
   * Workspace tab — with up to 3 equal columns. `canvas` is for the wide,
   * full-viewport doc-compare surface: natural height (so following content
   * like the bucket library isn't pushed off-screen) and a capped card width
   * (so cards don't balloon to the full column width and their aspect-3/4
   * thumbnails don't become enormous).
   */
  layout?: "panel" | "canvas";
  /**
   * The user's own documents, most recently worked with first
   * (`GET /api/documents/recent`). When the user has any, they REPLACE the
   * skill's shared demo `examples` — a returning user picks up their own work,
   * not the same five examples everyone sees. The demos stay as the first-run
   * fallback. Omit to keep the demo-only picker.
   */
  recent?: RecentDocument[];
  recentStatus?: RecentDocumentsStatus;
  onRetryRecent?: () => void;
  /** Open a recent document in the workbench (as included context). */
  onOpenRecent?: (doc: RecentDocument) => void;
  /** Continue the conversation a recent document was last used in. */
  onResumeSession?: (sessionId: string) => void;
  /** Heading for the recent section when it isn't "your documents" (e.g. a project's). */
  recentHeading?: { eyebrow: string; title: string; description: string };
  /** Shown instead of the demo examples when the recent list is empty — a
   * project with no documents says so, rather than offering demos. */
  recentEmpty?: ReactNode;
}

/**
 * SkillExamplesPicker (v6.4.0 4.5 SKILL-ONBOARDING M2).
 *
 * Card grid mounted in the WorkbenchPane Workspace tab when a chat is fresh
 * AND the active skill declares `welcome.example_documents`. Replaces the
 * EmptyTab fallback for skills that ship onboarding affordances; falls
 * through to EmptyTab when no examples set.
 *
 * Click → parent fires a chat message that asks the agent to load the
 * example via its bucket tools. No new backend endpoint required for v1;
 * the proper import-by-reference path can land later (4.5 M4 / v6.5).
 *
 * Q1 locked 2026-06-09: generic doc-icon fallback when example.thumbnail
 * is null. Auto-rendered thumbnails defer to v6.5.
 */
export function SkillExamplesPicker({
  examples,
  onPickExample,
  prompts = [],
  onPickPrompt,
  onUploadOwn,
  layout = "panel",
  recent,
  recentStatus = "ready",
  onRetryRecent,
  onOpenRecent,
  onResumeSession,
  recentHeading,
  recentEmpty,
}: SkillExamplesPickerProps) {
  const showPrompts = prompts.length > 0 && Boolean(onPickPrompt);
  const recentEnabled = recent !== undefined && Boolean(onOpenRecent);
  const hasRecent = recentEnabled && recent.length > 0;
  // Hold the demo cards back while the user's own list loads: showing the
  // demos and then swapping them out is a flash of the wrong content.
  const recentPending = recentEnabled && recentStatus === "loading";
  const showRecentEmpty = recentEnabled && recentEmpty !== undefined && recentStatus === "ready" && recent.length === 0;
  const showExamples = examples.length > 0 && !hasRecent && !recentPending && recentEmpty === undefined;
  if (examples.length === 0 && !showPrompts && !hasRecent && !recentPending && !showRecentEmpty) return null;
  const isCanvas = layout === "canvas";
  return (
    <div className={cn("flex flex-col gap-6 p-6", !isCanvas && "h-full overflow-auto")}>
      {recentPending && <RecentSkeleton isCanvas={isCanvas} />}
      {hasRecent && (
        <RecentDocuments
          docs={recent}
          isCanvas={isCanvas}
          onOpen={onOpenRecent!}
          onResumeSession={onResumeSession}
          heading={recentHeading}
        />
      )}
      {showRecentEmpty && (
        <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground" data-testid="recent-documents-empty">
          {recentEmpty}
        </p>
      )}
      {showPrompts && (
        <section className="space-y-3" data-testid="example-prompts">
          <div className="space-y-1">
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              Try one of these
            </p>
            <h3 className="text-lg font-semibold tracking-tight text-foreground">
              What this assistant can do
            </h3>
            <p className="text-sm text-muted-foreground">
              Each is a real run, not a canned demo — click one to watch it work.
            </p>
          </div>
          <ul className="grid gap-2 sm:grid-cols-2">
            {prompts.map((p, i) => (
              <li
                key={p.label}
                className="animate-in fade-in-0 slide-in-from-bottom-2 fill-mode-both"
                style={{ animationDelay: `${i * 50}ms`, animationDuration: "350ms" }}
              >
                <button
                  type="button"
                  onClick={() => onPickPrompt?.(p.prompt)}
                  title={p.prompt}
                  className="group flex h-full w-full flex-col gap-1 rounded-lg border border-border bg-background p-3 text-left shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:bg-muted/40 hover:shadow-md focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                >
                  {p.badge && (
                    <span className="w-fit rounded-md bg-primary/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-primary">
                      {p.badge}
                    </span>
                  )}
                  <span className="text-sm font-semibold leading-tight text-foreground group-hover:text-primary">
                    {p.label}
                  </span>
                  {p.summary && (
                    <span className="line-clamp-2 text-xs leading-snug text-muted-foreground">{p.summary}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {recentEnabled && recentStatus === "error" && (
        // NEVER SILENT (#8): say why the user is looking at examples rather
        // than their own documents, and offer the way back.
        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          <span>Couldn&apos;t load your recent documents — showing examples instead.</span>
          {onRetryRecent && (
            <button type="button" onClick={onRetryRecent} className="shrink-0 rounded border px-2 py-0.5 text-foreground hover:bg-accent">
              Retry
            </button>
          )}
        </div>
      )}

      {showExamples && (
        <div className="space-y-1">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            {showPrompts ? "Or start from a document" : "Try with an example"}
          </p>
          <h3 className="text-lg font-semibold tracking-tight text-foreground">
            Pick a document to get started
          </h3>
          <p className="text-sm text-muted-foreground">
            Each card below is a representative document the assistant can walk
            you through. You can also upload your own at any time.
          </p>
        </div>
      )}

      {showExamples && (
        <ul className={cardGrid(isCanvas)}>
          {examples.map((example, i) => (
            <li
              key={`${example.bucket}/${example.object}`}
              className="animate-in fade-in-0 slide-in-from-bottom-2 fill-mode-both"
              style={{ animationDelay: `${i * 60}ms`, animationDuration: "400ms" }}
            >
              <button
                type="button"
                onClick={() => onPickExample(example)}
                className="group flex h-full w-full flex-col gap-3 rounded-lg border border-border bg-background p-4 text-left shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:bg-muted/40 hover:shadow-md focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                <ExampleThumbnail example={example} />
                <div className="space-y-1">
                  <p className="text-sm font-semibold leading-tight text-foreground group-hover:text-primary">
                    {example.label}
                  </p>
                  {example.summary && (
                    <p className="line-clamp-2 text-xs leading-snug text-muted-foreground">
                      {example.summary}
                    </p>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {onUploadOwn && (
        <div className="border-t border-border pt-4">
          <button
            type="button"
            onClick={onUploadOwn}
            className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-primary"
          >
            Or upload your own document ↑
          </button>
        </div>
      )}
    </div>
  );
}

const cardGrid = (isCanvas: boolean) =>
  cn(
    "grid gap-3",
    isCanvas
      ? // Wide canvas: cap card width so cards + their aspect-3/4 thumbnails
        // stay compact instead of stretching across the whole viewport.
        "[grid-template-columns:repeat(auto-fill,minmax(11rem,13rem))]"
      : "grid-cols-1 sm:grid-cols-2 md:grid-cols-3",
  );

function RecentDocuments({
  docs,
  isCanvas,
  onOpen,
  onResumeSession,
  heading,
}: {
  docs: RecentDocument[];
  isCanvas: boolean;
  onOpen: (doc: RecentDocument) => void;
  onResumeSession?: (sessionId: string) => void;
  heading?: { eyebrow: string; title: string; description: string };
}) {
  return (
    <section className="space-y-3" data-testid="recent-documents" aria-labelledby="recent-documents-heading">
      <div className="space-y-1">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          {heading?.eyebrow ?? "Your documents"}
        </p>
        <h3 id="recent-documents-heading" className="text-lg font-semibold tracking-tight text-foreground">
          {heading?.title ?? "Pick up where you left off"}
        </h3>
        <p className="text-sm text-muted-foreground">
          {heading?.description ??
            "The documents you've worked with most recently. Open one to ask about it, or jump back into the conversation you had."}
        </p>
      </div>
      <ul className={cardGrid(isCanvas)}>
        {docs.map((doc, i) => (
          <li
            key={doc.id}
            className="animate-in fade-in-0 slide-in-from-bottom-2 fill-mode-both"
            style={{ animationDelay: `${i * 60}ms`, animationDuration: "400ms" }}
          >
            <div className="group relative flex h-full flex-col gap-3 rounded-lg border border-border bg-background p-4 shadow-sm transition-all duration-200 focus-within:border-primary hover:-translate-y-0.5 hover:border-primary/50 hover:bg-muted/40 hover:shadow-md">
              <button
                type="button"
                onClick={() => onOpen(doc)}
                aria-label={`Open ${doc.originalFilename}`}
                className="flex flex-col gap-3 text-left focus-visible:outline-none after:absolute after:inset-0 after:rounded-lg after:content-['']"
              >
                <div className="relative aspect-[3/4] overflow-hidden rounded-md border border-border bg-white">
                  <DocumentThumbnail
                    source={{ kind: "doc", docId: doc.id, format: doc.sourceFormat, version: doc.updatedAt }}
                    alt={`First page of ${doc.originalFilename}`}
                  />
                </div>
                <div className="space-y-1">
                  <p className="line-clamp-2 break-words text-sm font-semibold leading-tight text-foreground group-hover:text-primary">
                    {doc.originalFilename}
                  </p>
                  <p className="text-xs leading-snug text-muted-foreground">
                    {doc.lastSessionId ? "Last used" : "Added"} {relativeTime(doc.lastUsedAt)}
                  </p>
                </div>
              </button>
              {doc.lastSessionId && onResumeSession && (
                // Sits above the card's stretched link (relative z-10) so it is
                // its own target: open the doc, OR continue that conversation.
                <button
                  type="button"
                  onClick={() => onResumeSession(doc.lastSessionId!)}
                  title={doc.lastSessionTitle ?? undefined}
                  className="relative z-10 -mt-1 flex w-full items-center gap-1 rounded-md px-1 py-1 text-left text-xs text-muted-foreground hover:bg-background hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                >
                  <span aria-hidden>↩</span>
                  <span className="line-clamp-1">
                    Continue{doc.lastSessionTitle ? <> &ldquo;{doc.lastSessionTitle}&rdquo;</> : " the conversation"}
                  </span>
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentSkeleton({ isCanvas }: { isCanvas: boolean }) {
  return (
    <div role="status" aria-label="Loading your recent documents" className="space-y-3">
      <div className="h-4 w-48 animate-pulse rounded bg-muted" />
      <ul className={cardGrid(isCanvas)}>
        {[0, 1, 2].map((i) => (
          <li key={i} className="aspect-[3/4] animate-pulse rounded-lg border border-border bg-muted/50" />
        ))}
      </ul>
    </div>
  );
}

function ExampleThumbnail({ example }: { example: ExampleDocument }) {
  // A skill can pin an explicit (authenticated) thumbnail URL; otherwise the
  // shared DocumentThumbnail renders the first page via the auth-gated route.
  return (
    <div className="relative aspect-[3/4] overflow-hidden rounded-md border border-border bg-white">
      {example.thumbnail ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={example.thumbnail} alt={`First page of ${example.label}`} className="h-full w-full object-cover" />
      ) : (
        <DocumentThumbnail
          source={{ kind: "bucket", bucket: example.bucket, object: example.object }}
          alt={`First page of ${example.label}`}
        />
      )}
    </div>
  );
}
