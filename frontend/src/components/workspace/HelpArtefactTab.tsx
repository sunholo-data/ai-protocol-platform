// HelpArtefactTab — the workbench "Help" Result tab.
//
// When every knowledge_search hit is a user guide page, the backend marks the
// result `kind: "help"` and puts REFERENCES in the surface data model at `/help`:
// `[{slug, anchor, title}]` (private/knowledge/tools.py `help_pages`). This tab
// renders the cited page itself, with the same GuideBody as /help, loaded from
// the app's own build (/help-content/<slug>), so screenshots, notes and layout
// match the published guide and nothing the model or the KB returns is rendered
// as the page. Help links in chat answers open here too (lib/helpViewer).

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { GuideBody } from "@/components/help/GuideBody";
import { BRANDING } from "@/lib/branding";
import {
  helpHref,
  latestHelpRequest,
  parseHelpHref,
  subscribeHelpRequests,
  type HelpTarget,
} from "@/lib/helpViewer";
import { withAppName } from "@/lib/userGuideParse";
import { useSurfaceState } from "@/providers/SurfaceRegistry";

interface HelpRef extends HelpTarget {
  title?: string;
}

interface HelpPage {
  slug: string;
  title: string;
  summary?: string;
  body: string;
}

type Load = { status: "loading" } | { status: "error" } | { status: "ready"; page: HelpPage };

const pageCache = new Map<string, Promise<HelpPage>>();

function fetchPage(slug: string): Promise<HelpPage> {
  let p = pageCache.get(slug);
  if (!p) {
    p = fetch(`/help-content/${encodeURIComponent(slug)}`).then((r) => {
      if (!r.ok) throw new Error(`help page ${slug}: ${r.status}`);
      return r.json() as Promise<HelpPage>;
    });
    p.catch(() => pageCache.delete(slug));
    pageCache.set(slug, p);
  }
  return p;
}

/** Tests only. */
export function clearHelpPageCache(): void {
  pageCache.clear();
}

/** The element that actually scrolls this tab: itself, or the workbench pane around it. Never the page. */
function scrollParent(start: HTMLElement): HTMLElement {
  for (let n: HTMLElement | null = start; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
    const { overflowY } = getComputedStyle(n);
    if ((overflowY === "auto" || overflowY === "scroll") && n.scrollHeight > n.clientHeight) return n;
  }
  return start;
}

function refLabel(ref: HelpRef): string {
  // Index titles read "Page › Section"; the section is what tells matches apart.
  const t = (ref.title || ref.slug).split(" › ");
  return withAppName(t[t.length - 1], BRANDING.appName);
}

export function HelpArtefactTab({ surfaceId, className }: { surfaceId: string; className?: string }) {
  const state = useSurfaceState(surfaceId);
  const root = (state?.surface?.dataModel?.get("/") ?? null) as { help?: HelpRef[] } | null;
  const refs = (root?.help ?? []).filter((r) => r && /^[\w-]+$/.test(r.slug));
  const refsKey = refs.map((r) => helpHref(r)).join(" ");

  const [current, setCurrent] = useState<HelpTarget | null>(null);
  const [scrollNonce, setScrollNonce] = useState(0);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const scroller = useRef<HTMLDivElement>(null);
  const stickyBar = useRef<HTMLDivElement>(null);

  // A new answer (new refs) opens its best match.
  useEffect(() => {
    const first = refs[0];
    if (first) {
      setCurrent({ slug: first.slug, anchor: first.anchor ?? "" });
      setScrollNonce((n) => n + 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the refs' content
  }, [refsKey]);

  // Help links clicked in chat: the latest one on mount (this tab may mount
  // after the click switched to it), then each new one.
  useEffect(() => {
    const go = (t: HelpTarget) => {
      setCurrent({ slug: t.slug, anchor: t.anchor });
      setScrollNonce((n) => n + 1);
    };
    const pending = latestHelpRequest();
    if (pending) go(pending);
    return subscribeHelpRequests(go);
  }, []);

  const slug = current?.slug;
  useEffect(() => {
    if (!slug) return;
    let live = true;
    setLoad({ status: "loading" });
    fetchPage(slug).then(
      (page) => live && setLoad({ status: "ready", page }),
      () => live && setLoad({ status: "error" }),
    );
    return () => {
      live = false;
    };
  }, [slug]);

  // Once the page is on screen, bring the cited section into view.
  useEffect(() => {
    if (load.status !== "ready" || !scroller.current) return;
    // Scroll the workbench pane only: scrollIntoView would also scroll the page
    // and the chat beside it, pushing the app header and the answer out of view.
    const root = scroller.current;
    const anchor = current?.anchor;
    const el = anchor ? root.querySelector<HTMLElement>(`[id="${CSS.escape(anchor)}"]`) : null;
    const box = scrollParent(root);
    const align = () => {
      const sticky = stickyBar.current?.offsetHeight ?? 0;
      const top = el ? el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - sticky - 8 : 0;
      box.scrollTo({ top: Math.max(0, top) });
    };
    align();
    if (!el) return;
    // Screenshots above the section load after this and push it down: keep it
    // in place as they arrive, until the reader scrolls for themselves.
    let userScrolled = false;
    const stop = () => {
      userScrolled = true;
    };
    const realign = () => {
      if (!userScrolled) align();
    };
    const imgs = Array.from(root.querySelectorAll("img")).filter((img) => !img.complete);
    imgs.forEach((img) => img.addEventListener("load", realign));
    box.addEventListener("wheel", stop, { passive: true });
    box.addEventListener("touchmove", stop, { passive: true });
    box.addEventListener("keydown", stop);
    return () => {
      imgs.forEach((img) => img.removeEventListener("load", realign));
      box.removeEventListener("wheel", stop);
      box.removeEventListener("touchmove", stop);
      box.removeEventListener("keydown", stop);
    };
  }, [load, current?.anchor, scrollNonce]);

  // Links between guide pages stay in this tab.
  const onLink = useCallback((href: string) => {
    const t = parseHelpHref(href);
    if (!t) return false;
    setCurrent(t);
    setScrollNonce((n) => n + 1);
    return true;
  }, []);

  const appName = BRANDING.appName;
  const full = current ? helpHref(current) : "/help";

  return (
    <div ref={scroller} className={`flex h-full flex-col overflow-auto ${className ?? ""}`}>
      <div ref={stickyBar} className="sticky top-0 z-10 space-y-2 border-b border-border bg-background/95 px-4 py-2.5 backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted-foreground">User guide</span>
          <a
            href={full}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-medium text-primary hover:underline"
          >
            Open full page
          </a>
        </div>
        {refs.length > 1 ? (
          <div role="group" aria-label="Matching sections" className="flex flex-wrap gap-1.5">
            {refs.map((r) => {
              const active = current?.slug === r.slug && (current?.anchor ?? "") === (r.anchor ?? "");
              return (
                <button
                  key={helpHref(r)}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onLink(helpHref(r))}
                  className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
                    active ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                  }`}
                >
                  {refLabel(r)}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      <div className="px-4 py-4">
        {!current ? (
          <p className="text-sm text-muted-foreground">No help pages were found for this answer.</p>
        ) : load.status === "loading" ? (
          <p className="text-sm text-muted-foreground" role="status">
            Loading help page…
          </p>
        ) : load.status === "error" ? (
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load this help page.{" "}
            <a href={full} target="_blank" rel="noopener noreferrer" className="underline">
              Open it in a new tab
            </a>
            .
          </p>
        ) : (
          <article className="space-y-4">
            <header className="space-y-1">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">{withAppName(load.page.title, appName)}</h1>
              {load.page.summary ? <p className="text-sm text-muted-foreground">{withAppName(load.page.summary, appName)}</p> : null}
            </header>
            <GuideBody body={load.page.body} appName={appName} onLink={onLink} compact />
          </article>
        )}
      </div>
    </div>
  );
}
