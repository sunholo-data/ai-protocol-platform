// docs/design/v6.1.0/ttft-instrumentation.md M3 — frontend latency store.
//
// Captures per-message timings on the client side and exposes them to the
// dev-only LatencyHUD. This is the *perceived* axis of TTFT — how long
// from the user hitting Enter to first DOM paint of stage label / first
// model token. The backend LatencyTracker captures the *real* axis; the
// HUD shows both side by side.
//
// Module-level state + useSyncExternalStore so the store works without a
// Provider wrapper. The HUD is the only consumer; we're not building a
// general-purpose state library.

export interface LatencyMark {
  /** Random id so React can key the row. */
  id: string;
  /** Session/thread the marks belong to. Marks for prior sessions are
   *  evicted on session change. */
  sessionId: string;
  /** performance.now() at the moment sendMessage started. */
  tSend: number;
  /** performance.now() at the first AG-UI event of the run (RUN_STARTED).
   *  Captures the round-trip + handshake time. */
  tFirstEvent: number | null;
  /** performance.now() at the first STAGE_PROGRESS Custom event arrival —
   *  the actual moment the user sees something other than dots. */
  tFirstStageLabel: number | null;
  /** performance.now() at the first TEXT_MESSAGE_CONTENT delta. The
   *  perceived TTFT in the strict sense — first word lands. */
  tFirstTextChunk: number | null;
  /** Server-authored payload from a LATENCY_REPORT Custom event, if one
   *  arrived (only when ?probe=1 was set). Null in normal chat traffic. */
  serverReport: Record<string, unknown> | null;
}

type Listener = () => void;

const MAX_MARKS = 20;

let _marks: LatencyMark[] = [];
let _activeSessionId: string | null = null;
const _listeners = new Set<Listener>();

function _emit() {
  for (const l of _listeners) l();
}

export function startMark(sessionId: string, id: string, tSend: number): void {
  // Session change — evict prior marks. Keeps the HUD focused on the
  // current conversation; cross-session comparisons aren't useful.
  if (sessionId !== _activeSessionId) {
    _marks = [];
    _activeSessionId = sessionId;
  }
  _marks = [
    ...(_marks.length >= MAX_MARKS ? _marks.slice(_marks.length - MAX_MARKS + 1) : _marks),
    {
      id,
      sessionId,
      tSend,
      tFirstEvent: null,
      tFirstStageLabel: null,
      tFirstTextChunk: null,
      serverReport: null,
    },
  ];
  _emit();
}

function _patchLatest(patch: Partial<LatencyMark>): void {
  if (_marks.length === 0) return;
  const idx = _marks.length - 1;
  const cur = _marks[idx];
  // Only set fields that aren't already populated — first-observation
  // wins, mirrors backend tracker semantics.
  const next: LatencyMark = { ...cur };
  for (const k of Object.keys(patch) as (keyof LatencyMark)[]) {
    if (next[k] === null || next[k] === undefined) {
      // Type-safe assignment via index signature; both source and target
      // narrow to LatencyMark.
      (next as unknown as Record<string, unknown>)[k] = patch[k] as unknown;
    }
  }
  _marks = [..._marks.slice(0, idx), next];
  _emit();
}

export function recordFirstEvent(t: number): void {
  _patchLatest({ tFirstEvent: t });
}

export function recordFirstStageLabel(t: number): void {
  _patchLatest({ tFirstStageLabel: t });
}

export function recordFirstTextChunk(t: number): void {
  _patchLatest({ tFirstTextChunk: t });
}

export function recordServerReport(payload: Record<string, unknown>): void {
  _patchLatest({ serverReport: payload });
}

export function clearMarks(): void {
  _marks = [];
  _activeSessionId = null;
  _emit();
}

// --- React subscription ---

export function subscribeLatencyStore(listener: Listener): () => void {
  _listeners.add(listener);
  return () => {
    _listeners.delete(listener);
  };
}

export function getLatencyMarks(): LatencyMark[] {
  return _marks;
}
