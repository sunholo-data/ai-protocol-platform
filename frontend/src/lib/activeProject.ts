/**
 * The project the open chat is in, for chrome outside ChatShell — the header's
 * "New chat" — which must keep a new chat in the project (leaving is explicit,
 * the banner's ✕). ChatShell owns the value; this is only where others read it.
 */
let active: string | null = null;

export function setActiveProject(projectId: string | null): void {
  active = projectId;
}

export function getActiveProject(): string | null {
  return active;
}
