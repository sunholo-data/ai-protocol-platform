// PPA-COMPARE-LAUNCHER M2 — Compare launcher visibility predicate.
//
// Extracted from ChatShell's WorkbenchPane so the gating is unit-testable
// without rendering the whole chat page. The launcher belongs to the Workspace
// tab's HOME for a launcher-capable skill (one-doc-compare,
// one-obligation-analysis) — and, since v6.23.0 WORKSPACE-HOME-PERSISTENCE, it
// stays there for the whole conversation.
//
// v6.23.0: the `artifactCount === 0 && !workspaceHasContent` gate is GONE.
// It existed because Home and Results were the same tab, so a launcher left
// visible would have fought the result for one pane. Now Home is a permanent
// tab and every result (including the once-exempt dominant `workspace` surface)
// gets its own Result tab, so there is nothing to fight over. Dana raised the
// eviction four times in the 2026-08-06 UAT — "if you want to keep the same
// conversation but use a new skill, you cannot do that in the Workspace
// anymore". Deleting the gate is the fix.
//
// Result auto-focus is unaffected: a new artifact still takes the stage
// (repo principle #7). Home is retained, not prioritised — one click away
// instead of gone.
//
// `optedIn` (allow_action_triggered_runs) alone is NOT a launcher signal: since
// 44c426c every front door that delegates to form-producing specialists carries
// that grant purely so elicitation-form submits pass gate 8. The door showing a
// doc-compare pane it has no tool for (and hiding its first-look prompt cards)
// was the 2026-07-21 regression. The launcher therefore also requires the skill
// to actually own a launcher-capable tool (`compare_ppa_contracts` /
// `map_ppa_obligations` — the same signals that size the doc picker).

export interface CompareLauncherVisibilityInput {
  /** toolConfigs.a2ui.allow_action_triggered_runs for the active skill. */
  optedIn: boolean;
  /** Skill declares `compare_ppa_contracts` (two-doc compare launcher). */
  allowCompare: boolean;
  /** Skill declares `map_ppa_obligations` (single-doc obligations launcher). */
  allowObligations: boolean;
}

/**
 * True when the workbench Compare launcher should render on the Workspace Home
 * tab: the skill is opted in AND owns a launcher-capable tool. Independent of
 * how many results the session has produced — that is the whole point.
 */
export function shouldShowCompareLauncher({
  optedIn,
  allowCompare,
  allowObligations,
}: CompareLauncherVisibilityInput): boolean {
  return optedIn && (allowCompare || allowObligations);
}
