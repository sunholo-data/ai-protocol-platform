// v6.23.0 B4 — "Workspace tab sometimes needs a second click."
//
// Extracted from ChatShell's WorkbenchPane so the auto-focus rule is testable
// without rendering the whole chat page (same reason, and the same shape, as
// lib/compareLauncher.ts).
//
// Repo principle #7 says "auto-focus new workbench elements". The bug was in
// what "new" meant. The effect used to compare the current artifact ids against
// *the previous render's* ids:
//
//     const hasNew = current.some((id) => !prevIds.has(id));
//     prevIds = currentIds;                       // ← replaced, not accumulated
//
// `SurfaceRegistry.listArtifacts()` only returns surfaces whose `state.surface`
// is non-null, so an artifact drops out of that list for as long as its surface
// is being re-registered (a replay, a re-emission, a rehydrate on RUN_FINISHED).
// One render later it is back — and because `prevIds` had been overwritten with
// the set that lacked it, it read as brand NEW and stole focus a second time.
//
// If the user had clicked Workspace in between, that steal silently undid their
// click. They click again, and it "works" — which is exactly how the bug was
// reported: Dana (Mark had seen it too), ONE UAT 2026-08-06, and reproduced
// live on 2026-08-07 with a prices result open.
//
// So: "new" means NEVER SEEN in this session. The seen set only ever grows,
// except when the user explicitly CLOSES a result — a close is deliberate, so a
// later re-emission of that result is a real arrival and should take the stage.
// `ChatShell.handleCloseResult` calls `forgetFocusedResult` for that.

/**
 * Decide which result tab (if any) should take focus this render.
 *
 * @param currentIds  Result tab ids currently present, in display order — the
 *                    LAST one wins when several arrive together (extract →
 *                    extract → compare: focus tracks the latest).
 * @param seen        Ids already auto-focused this session. **Mutated**: every
 *                    id in `currentIds` is added. Callers hold this in a ref.
 * @returns The id to focus, or `null` to leave focus exactly where it is.
 */
export function nextFocusedResult(currentIds: readonly string[], seen: Set<string>): string | null {
  let focusId: string | null = null;
  for (const id of currentIds) {
    if (!seen.has(id)) {
      seen.add(id);
      focusId = id;
    }
  }
  return focusId;
}

/**
 * Release the auto-focus latch for a result the user explicitly closed, so a
 * re-emission counts as a genuinely new arrival rather than a blink.
 */
export function forgetFocusedResult(id: string, seen: Set<string>): void {
  seen.delete(id);
}
