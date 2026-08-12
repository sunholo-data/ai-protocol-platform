import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The HUD reads NEXT_PUBLIC_DEV_LATENCY_HUD at module-import time.
// Tests must set the var BEFORE importing the component, then re-import
// per-test via vi.resetModules() to flip the env-flag branch.

afterEach(() => {
  vi.resetModules();
  delete process.env.NEXT_PUBLIC_DEV_LATENCY_HUD;
});

describe("LatencyHUD — env-flag gating", () => {
  it("renders nothing when NEXT_PUBLIC_DEV_LATENCY_HUD is unset", async () => {
    vi.resetModules();
    delete process.env.NEXT_PUBLIC_DEV_LATENCY_HUD;
    const { LatencyHUD } = await import("../LatencyHUD");
    const { container } = render(<LatencyHUD />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when env var is set to anything other than '1'", async () => {
    vi.resetModules();
    process.env.NEXT_PUBLIC_DEV_LATENCY_HUD = "true";
    const { LatencyHUD } = await import("../LatencyHUD");
    const { container } = render(<LatencyHUD />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the HUD shell when env var is exactly '1'", async () => {
    vi.resetModules();
    process.env.NEXT_PUBLIC_DEV_LATENCY_HUD = "1";
    const { LatencyHUD } = await import("../LatencyHUD");
    render(<LatencyHUD />);
    expect(screen.getByTestId("latency-hud")).toBeInTheDocument();
    // Empty-state hint when no marks have been recorded.
    expect(screen.getByText(/Send a message/i)).toBeInTheDocument();
  });
});

describe("LatencyHUD — mark rendering", () => {
  beforeEach(() => {
    vi.resetModules();
    process.env.NEXT_PUBLIC_DEV_LATENCY_HUD = "1";
  });

  it("renders a row per mark with perceived ms values", async () => {
    const store = await import("@/stores/latencyStore");
    store.clearMarks();
    store.startMark("session-a", "msg-1", 100);
    store.recordFirstEvent(150);
    store.recordFirstStageLabel(170);
    store.recordFirstTextChunk(540);

    const { LatencyHUD } = await import("../LatencyHUD");
    render(<LatencyHUD />);

    const rows = screen.getAllByTestId("latency-hud-row");
    expect(rows).toHaveLength(1);
    // 540 - 100 = 440ms perceived chunk time
    expect(rows[0]).toHaveTextContent("440ms");
    // 50ms perceived first event
    expect(rows[0]).toHaveTextContent("50ms");
  });

  it("displays model + routing from LATENCY_REPORT payload", async () => {
    const store = await import("@/stores/latencyStore");
    store.clearMarks();
    store.startMark("session-a", "msg-1", 100);
    store.recordServerReport({
      first_model_token_ms: 487,
      model_used: "gemini-2.5-flash",
      routing_choice: "fast",
    });

    const { LatencyHUD } = await import("../LatencyHUD");
    render(<LatencyHUD />);
    expect(screen.getByText("gemini-2.5-flash")).toBeInTheDocument();
    expect(screen.getByText("fast")).toBeInTheDocument();
    // Real first-token-ms surfaces in the rightmost column.
    const rows = screen.getAllByTestId("latency-hud-row");
    expect(rows[0]).toHaveTextContent("487ms");
  });

  it("shows '—' for missing measurements", async () => {
    const store = await import("@/stores/latencyStore");
    store.clearMarks();
    store.startMark("session-a", "msg-1", 100);
    // Only tFirstEvent set; chunk + label remain null.
    store.recordFirstEvent(150);

    const { LatencyHUD } = await import("../LatencyHUD");
    render(<LatencyHUD />);
    const rows = screen.getAllByTestId("latency-hud-row");
    // Empty cells render as '—'.
    expect(rows[0]).toHaveTextContent("—");
  });

  it("shows the most recent 5 marks, latest first", async () => {
    const store = await import("@/stores/latencyStore");
    store.clearMarks();
    for (let i = 0; i < 8; i++) {
      store.startMark("session-a", `msg-${i}`, i * 100);
      store.recordFirstTextChunk(i * 100 + 50);
    }

    const { LatencyHUD } = await import("../LatencyHUD");
    render(<LatencyHUD />);
    const rows = screen.getAllByTestId("latency-hud-row");
    expect(rows).toHaveLength(5);
  });
});
