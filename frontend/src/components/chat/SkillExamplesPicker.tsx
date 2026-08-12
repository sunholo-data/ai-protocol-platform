"use client";

import type { ExampleDocument, ExamplePrompt } from "@/types/skill";
import { cn } from "@/lib/utils";
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
}: SkillExamplesPickerProps) {
  const showPrompts = prompts.length > 0 && Boolean(onPickPrompt);
  if (examples.length === 0 && !showPrompts) return null;
  const isCanvas = layout === "canvas";
  return (
    <div className={cn("flex flex-col gap-6 p-6", !isCanvas && "h-full overflow-auto")}>
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

      {examples.length > 0 && (
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

      <ul
        className={cn(
          "grid gap-3",
          examples.length === 0 && "hidden",
          isCanvas
            ? // Wide canvas: cap card width so cards + their aspect-3/4 thumbnails
              // stay compact instead of stretching across the whole viewport.
              "[grid-template-columns:repeat(auto-fill,minmax(11rem,13rem))]"
            : "grid-cols-1 sm:grid-cols-2 md:grid-cols-3",
        )}
      >
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
