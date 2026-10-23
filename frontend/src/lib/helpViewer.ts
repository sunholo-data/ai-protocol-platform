/**
 * Help links in the app's own content: `/help/<slug>#<anchor>`.
 *
 * Chat answers from the guide skill link to help pages. Those links open the page
 * in the workbench Help tab when the chat has one (ChatShell registers an opener),
 * and in a new browser tab otherwise, so a help link always goes somewhere.
 */

export interface HelpTarget {
  slug: string;
  anchor: string;
}

const HELP_PATH = /^\/help\/([\w-]+)\/?(?:#([\w-]+))?$/;

/** `/help/folders#upload` → {slug, anchor}; anything else → null. */
export function parseHelpHref(href: string): HelpTarget | null {
  const m = HELP_PATH.exec(href);
  return m ? { slug: m[1], anchor: m[2] ?? "" } : null;
}

export function helpHref({ slug, anchor }: HelpTarget): string {
  return `/help/${slug}${anchor ? `#${anchor}` : ""}`;
}

/** An app-relative path (`/chat/...`), not a protocol-relative `//host` URL. */
export function isAppPath(href: string): boolean {
  return href.startsWith("/") && !href.startsWith("//") && !href.startsWith("/\\");
}

type Opener = (target: HelpTarget) => void;
type Listener = (request: HelpRequest) => void;

export interface HelpRequest extends HelpTarget {
  /** Changes on every request, so asking for the same section twice still scrolls. */
  nonce: number;
}

let opener: Opener | null = null;
let latest: HelpRequest | null = null;
let nonce = 0;
const listeners = new Set<Listener>();

/** ChatShell sets this while the chat shows a Help tab; null when it has none. */
export function setHelpOpener(next: Opener | null): void {
  opener = next;
}

/** Open a help section in the workbench. False when there is no Help tab to open it in. */
export function openHelpInWorkbench(target: HelpTarget): boolean {
  if (!opener) return false;
  latest = { ...target, nonce: ++nonce };
  listeners.forEach((l) => l(latest!));
  opener(target);
  return true;
}

/** The Help tab follows requests; it gets the latest one on mount (it may mount after the click). */
export function subscribeHelpRequests(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function latestHelpRequest(): HelpRequest | null {
  return latest;
}

/** Tests only. */
export function resetHelpViewer(): void {
  opener = null;
  latest = null;
  listeners.clear();
}
