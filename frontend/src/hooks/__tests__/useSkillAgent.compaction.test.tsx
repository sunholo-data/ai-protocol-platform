// useSkillAgent — the composer is released when the ANSWER is done, not when
// the run is (COMPACTION-LATENCY M2).
//
// Measured 2026-08-06: after a compacting turn's answer had fully rendered, the
// composer stayed disabled and the typing indicator kept spinning for a median
// of 37s (max 47s) while a summarisation ran. `isLoading` clears on
// `onRunFinalized`, and the run does not finalise until compaction completes.
//
// `HISTORY_COMPACTED` cannot fix this: it fires when summarisation RETURNS,
// ~35s later, at roughly the same moment as RUN_FINISHED. `COMPACTION_STARTED`
// fires BEFORE the model call — the only position from which it can release the
// user — and `tidyingUp` drives an honest notice rather than a silent unlock.

import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

type CustomHandler = (e: { event: { name?: unknown; value?: unknown } }) => void;
const customHandlers: CustomHandler[] = [];

const agentStub = {
  threadId: "t-1",
  messages: [],
  runAgent: vi.fn().mockResolvedValue(undefined),
  abortRun: vi.fn(),
  subscribe: ({ onCustomEvent }: { onCustomEvent?: CustomHandler }) => {
    if (onCustomEvent) customHandlers.push(onCustomEvent);
    return { unsubscribe: () => {} };
  },
};

vi.mock("@/providers/AGUIProvider", () => ({
  useAGUIAgent: () => agentStub,
}));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
}));

vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceRegistry: () => ({ appendMessages: vi.fn(), clearSurface: vi.fn() }),
  useOptionalSurfaceRegistry: () => null,
}));

import { useSkillAgent } from "@/hooks/useSkillAgent";

function emit(name: string, value: Record<string, unknown> = {}) {
  act(() => {
    for (const h of customHandlers) h({ event: { name, value } });
  });
}

beforeEach(() => {
  customHandlers.length = 0;
  vi.clearAllMocks();
});

describe("useSkillAgent — compaction releases the composer (M2)", () => {
  it("starts with tidyingUp false", () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(result.current.tidyingUp).toBe(false);
  });

  it("COMPACTION_STARTED sets tidyingUp", () => {
    const { result } = renderHook(() => useSkillAgent());
    emit("COMPACTION_STARTED", { events_to_compact: 12 });
    expect(result.current.tidyingUp).toBe(true);
  });

  it("COMPACTION_STARTED clears isLoading so the composer re-enables", () => {
    const { result } = renderHook(() => useSkillAgent());
    emit("COMPACTION_STARTED", { events_to_compact: 12 });
    // The regression this milestone exists for: 37s median of a disabled
    // composer with the answer already on screen.
    expect(result.current.isLoading).toBe(false);
  });

  it("HISTORY_COMPACTED clears tidyingUp", () => {
    const { result } = renderHook(() => useSkillAgent());
    emit("COMPACTION_STARTED", { events_to_compact: 12 });
    emit("HISTORY_COMPACTED", { events_compacted: 12, summary_chars: 900 });
    expect(result.current.tidyingUp).toBe(false);
  });

  it("still records the compaction marker for the Activity feed", () => {
    const { result } = renderHook(() => useSkillAgent());
    emit("COMPACTION_STARTED", { events_to_compact: 12 });
    emit("HISTORY_COMPACTED", { events_compacted: 12, summary_chars: 900 });
    // Releasing the composer must not cost the observability M4 added.
    expect(result.current.compactions).toHaveLength(1);
    expect(result.current.compactions[0].eventsCompacted).toBe(12);
  });

  it("COMPACTION_STARTED does not itself create a compaction marker", () => {
    // A compaction that started is not a compaction that happened — marking it
    // here would double-count, and would claim history was lost before it was.
    const { result } = renderHook(() => useSkillAgent());
    emit("COMPACTION_STARTED", { events_to_compact: 12 });
    expect(result.current.compactions).toHaveLength(0);
  });

  it("exposes tidyingUp on the documented public surface", () => {
    const { result } = renderHook(() => useSkillAgent());
    expect(Object.keys(result.current)).toContain("tidyingUp");
  });
});
