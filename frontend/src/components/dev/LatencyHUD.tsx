// docs/design/v6.1.0/ttft-instrumentation.md M3 — developer latency HUD.
//
// Fixed-position panel, bottom-right, gated by NEXT_PUBLIC_DEV_LATENCY_HUD=1.
// Shows real (server) and perceived (client) TTFT side by side for the last
// few chat messages. Reads from latencyStore via useSyncExternalStore so it
// stays in sync without a Provider wrapper.
//
// When the env var is unset the component returns null in the same render
// pass — Next.js tree-shakes the JSX body for prod builds.

"use client";

import { useSyncExternalStore } from "react";
import {
  getLatencyMarks,
  type LatencyMark,
  subscribeLatencyStore,
} from "@/stores/latencyStore";

const ENABLED = process.env.NEXT_PUBLIC_DEV_LATENCY_HUD === "1";

function fmtMs(t: number | null | undefined): string {
  if (t === null || t === undefined) return "—";
  if (typeof t !== "number" || !Number.isFinite(t)) return "—";
  return `${t.toFixed(0)}ms`;
}

function perceived(mark: LatencyMark): {
  toFirstEvent: number | null;
  toFirstLabel: number | null;
  toFirstChunk: number | null;
} {
  return {
    toFirstEvent: mark.tFirstEvent !== null ? mark.tFirstEvent - mark.tSend : null,
    toFirstLabel:
      mark.tFirstStageLabel !== null ? mark.tFirstStageLabel - mark.tSend : null,
    toFirstChunk:
      mark.tFirstTextChunk !== null ? mark.tFirstTextChunk - mark.tSend : null,
  };
}

function serverFirstTokenMs(mark: LatencyMark): number | null {
  const r = mark.serverReport;
  if (!r) return null;
  const v = r["first_model_token_ms"];
  return typeof v === "number" ? v : null;
}

function serverModelUsed(mark: LatencyMark): string | null {
  const r = mark.serverReport;
  if (!r) return null;
  const v = r["model_used"];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function serverRouting(mark: LatencyMark): string | null {
  const r = mark.serverReport;
  if (!r) return null;
  const v = r["routing_choice"];
  return typeof v === "string" && v.length > 0 ? v : null;
}

export function LatencyHUD() {
  // Subscribe even when ENABLED is false so the hook count is stable.
  // The early return below kicks the component out of the React tree
  // when the env flag is unset; useSyncExternalStore is cheap.
  const marks = useSyncExternalStore(
    subscribeLatencyStore,
    getLatencyMarks,
    getLatencyMarks,
  );

  if (!ENABLED) return null;
  if (marks.length === 0) {
    return (
      <div
        data-testid="latency-hud"
        className="fixed bottom-4 right-4 z-50 max-w-xs rounded-md border border-orange-300 bg-white p-2 text-xs font-mono shadow-md"
      >
        <p className="font-semibold text-orange-700">TTFT HUD</p>
        <p className="text-muted-foreground">
          Send a message to see perceived &amp; real TTFT.
        </p>
      </div>
    );
  }

  // Most recent first.
  const recent = [...marks].reverse().slice(0, 5);
  const latest = recent[0];

  return (
    <div
      data-testid="latency-hud"
      className="fixed bottom-4 right-4 z-50 max-w-md space-y-1 rounded-md border border-orange-300 bg-white p-2 text-xs font-mono shadow-md"
    >
      <p className="font-semibold text-orange-700">TTFT HUD — last {recent.length}</p>

      <div className="grid grid-cols-[auto_1fr] gap-x-2 border-b pb-1">
        <span className="text-muted-foreground">model</span>
        <span>{serverModelUsed(latest) ?? "—"}</span>
        <span className="text-muted-foreground">routing</span>
        <span>{serverRouting(latest) ?? "—"}</span>
      </div>

      <table className="w-full">
        <thead className="text-muted-foreground">
          <tr>
            <th className="text-left font-normal">#</th>
            <th className="text-right font-normal" title="Perceived: t_send → first AG-UI event">
              evt
            </th>
            <th className="text-right font-normal" title="Perceived: t_send → first stage label">
              lbl
            </th>
            <th
              className="text-right font-normal"
              title="Perceived: t_send → first text chunk (the strict perceived TTFT)"
            >
              chunk
            </th>
            <th
              className="text-right font-normal"
              title="Real: backend first_model_token_ms (only when ?probe=1)"
            >
              real
            </th>
          </tr>
        </thead>
        <tbody>
          {recent.map((m, idx) => {
            const p = perceived(m);
            const real = serverFirstTokenMs(m);
            return (
              <tr key={m.id} data-testid="latency-hud-row">
                <td className="text-left text-muted-foreground">
                  {recent.length - idx}
                </td>
                <td className="text-right">{fmtMs(p.toFirstEvent)}</td>
                <td className="text-right">{fmtMs(p.toFirstLabel)}</td>
                <td className="text-right font-semibold">
                  {fmtMs(p.toFirstChunk)}
                </td>
                <td className="text-right text-muted-foreground">
                  {fmtMs(real)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
