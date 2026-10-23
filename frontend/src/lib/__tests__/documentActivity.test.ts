// v6.34.0 DOC-WORKBENCH — document edits appear in Activity.
//
// The workbench panels call REST endpoints directly rather than going through
// the agent. That is what makes them fast, and it is also why they were
// invisible: a rewrite that took a few seconds produced no trace anywhere, so
// the run looked like nothing happening (principle #8 wants live progress, not
// only a terminal state).

import { describe, it, expect, vi } from "vitest";
import { reportDocumentAction } from "../documentActivity";

describe("reportDocumentAction", () => {
  it("reports the action as running straight away", () => {
    const upsertToolCall = vi.fn();
    reportDocumentAction({ upsertToolCall }, "rewrite_passage", "make it concise");

    expect(upsertToolCall).toHaveBeenCalledTimes(1);
    const call = upsertToolCall.mock.calls[0][0];
    expect(call.name).toBe("rewrite_passage");
    expect(call.status).toBe("running");
    expect(call.resultContent).toBe("make it concise");
  });

  it("settles the SAME entry rather than adding a second", () => {
    const upsertToolCall = vi.fn();
    const settle = reportDocumentAction({ upsertToolCall }, "restyle_document");
    settle({ ok: true, message: "Restyled" });

    expect(upsertToolCall).toHaveBeenCalledTimes(2);
    const [first, second] = upsertToolCall.mock.calls.map((c) => c[0]);
    expect(second.id).toBe(first.id);
    expect(second.status).toBe("success");
    expect(second.resultContent).toBe("Restyled");
  });

  it("records a failure as an error, with its reason", () => {
    const upsertToolCall = vi.fn();
    const settle = reportDocumentAction({ upsertToolCall }, "restore_version", "version 2");
    settle({ ok: false, message: "Version 2 is no longer available." });

    const last = upsertToolCall.mock.calls.at(-1)![0];
    expect(last.status).toBe("error");
    expect(last.resultContent).toContain("no longer available");
  });

  it("keeps the start time on the settled entry so ordering is stable", () => {
    const upsertToolCall = vi.fn();
    const settle = reportDocumentAction({ upsertToolCall }, "rewrite_passage");
    settle({ ok: true });

    const [first, second] = upsertToolCall.mock.calls.map((c) => c[0]);
    expect(second.ts).toBe(first.ts);
  });

  it("gives concurrent actions distinct ids", () => {
    const upsertToolCall = vi.fn();
    reportDocumentAction({ upsertToolCall }, "rewrite_passage");
    reportDocumentAction({ upsertToolCall }, "rewrite_passage");

    const [a, b] = upsertToolCall.mock.calls.map((c) => c[0]);
    expect(a.id).not.toBe(b.id);
  });

  it("is a no-op without a sink — reporting must never break the action", () => {
    expect(() => reportDocumentAction(undefined, "rewrite_passage")({ ok: true })).not.toThrow();
    expect(() => reportDocumentAction({}, "rewrite_passage")({ ok: false })).not.toThrow();
  });
});
