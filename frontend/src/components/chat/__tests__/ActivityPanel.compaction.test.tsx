// ActivityPanel — the "History summarised" marker (COMPACTION-WIRE M4).
//
// Compaction rewrites what the assistant can remember while the user keeps
// seeing the full transcript. Without a visible marker a degraded answer is
// indistinguishable from a good one — which is exactly how the 2026-08-06 UAT
// issue went undiagnosed and why this sprint had to reverse-engineer an
// invisible mechanism out of raw session events.
//
// CLAUDE.md #8 (NEVER SILENT), applied to context rather than to actions.

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActivityPanel } from "@/components/chat/ActivityPanel";

vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => null,
}));

vi.mock("@/components/protocols/A2UISurfaceMount", () => ({
  A2UISurfaceMount: () => null,
}));

vi.mock("@/lib/apiClient", () => ({
  fetchWithAuth: vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }),
}));

function renderPanel(props: Partial<React.ComponentProps<typeof ActivityPanel>> = {}) {
  return render(
    <ActivityPanel
      toolCalls={[]}
      delegations={[]}
      isThinking={false}
      sessionId="t-1"
      {...props}
    />,
  );
}

describe("ActivityPanel — compaction marker", () => {
  it("renders a marker when history was compacted", () => {
    renderPanel({
      compactions: [{ id: "c-1", ts: Date.now(), eventsCompacted: 18, summaryChars: 900 }],
    });
    expect(screen.getByText(/history summarised/i)).toBeTruthy();
  });

  it("says how much was condensed", () => {
    renderPanel({
      compactions: [{ id: "c-1", ts: Date.now(), eventsCompacted: 18, summaryChars: 900 }],
    });
    // The count is the actionable part — it tells a triager how much of the
    // conversation the model can no longer see verbatim.
    expect(screen.getByText(/18 earlier entries/i)).toBeTruthy();
  });

  it("renders nothing compaction-related when none occurred", () => {
    // Guards a marker that renders unconditionally, which would be worse than
    // no marker: it would imply context loss in every healthy conversation.
    renderPanel({ compactions: [] });
    expect(screen.queryByText(/history summarised/i)).toBeNull();
  });

  it("is absent when the prop is omitted entirely", () => {
    renderPanel();
    expect(screen.queryByText(/history summarised/i)).toBeNull();
  });

  it("renders one marker per compaction", () => {
    renderPanel({
      compactions: [
        { id: "c-1", ts: Date.now() - 60_000, eventsCompacted: 12, summaryChars: 400 },
        { id: "c-2", ts: Date.now(), eventsCompacted: 20, summaryChars: 800 },
      ],
    });
    expect(screen.getAllByText(/history summarised/i)).toHaveLength(2);
  });

  it("uses singular wording for a single entry", () => {
    renderPanel({
      compactions: [{ id: "c-1", ts: Date.now(), eventsCompacted: 1, summaryChars: 50 }],
    });
    expect(screen.getByText(/1 earlier entry/i)).toBeTruthy();
  });
});
