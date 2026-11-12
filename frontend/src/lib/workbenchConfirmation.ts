/** Server-persisted assistant replies from manual workbench operations. */
export interface WorkbenchConfirmation { sessionId: string; id: string; content: string }
const listeners = new Set<(reply: WorkbenchConfirmation) => void>();
export function emitWorkbenchConfirmation(reply: WorkbenchConfirmation): void {
  for (const listener of listeners) listener(reply);
}
export function subscribeWorkbenchConfirmation(listener: (reply: WorkbenchConfirmation) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
