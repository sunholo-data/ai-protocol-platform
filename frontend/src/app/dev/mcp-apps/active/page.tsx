// /dev/mcp-apps/active — full active iframe → host bridge.
//
// Mounts MCPAppToolCallRouter with a stubbed onChatMessage that appends
// to an on-page log instead of calling sendMessage. Plus a panel of
// buttons that synthesise common iframe notifications and route them
// through the same notification adapter, so you can exercise the bridge
// without a running iframe.
//
// Run: open http://localhost:3456/dev/mcp-apps/active

"use client";

import { useState } from "react";
import { MCPAppToolCallRouter } from "@/components/protocols/MCPAppToolCallRouter";
import { notificationToChatMessage } from "@/components/protocols/mcpAppNotificationAdapter";
import type { ToolCallState } from "@/hooks/useSkillAgent";
import showMapResult from "@/components/protocols/__tests__/fixtures/map-server-show-map-result.json";

const FIXTURE_TOOL_CALL: ToolCallState = {
  id: "fixture-show-map-1",
  name: "show-map",
  status: "success",
  parentMessageId: "fixture-asst-1",
  argsJson: JSON.stringify({
    west: 11.4,
    south: 48.0,
    east: 11.7,
    north: 48.2,
    label: "Munich",
  }),
  resultContent: JSON.stringify(showMapResult.result),
};

interface SyntheticNotification {
  label: string;
  payload: unknown;
}

const SYNTHETIC_NOTIFICATIONS: readonly SyntheticNotification[] = [
  {
    label: "location-selected (Munich)",
    payload: { type: "app/notify", reason: "location-selected", payload: { location: "Munich" } },
  },
  {
    label: "location-selected (Paris)",
    payload: { type: "app/notify", reason: "location-selected", payload: { location: "Paris" } },
  },
  {
    label: "route-selected (Munich → Paris)",
    payload: { type: "app/notify", reason: "route-selected", payload: { from: "Munich", to: "Paris" } },
  },
  {
    label: "unknown-shape",
    payload: { type: "app/notify", reason: "spaceship-launched", payload: { destination: "Mars" } },
  },
  {
    label: "malformed (missing payload)",
    payload: { type: "app/notify", reason: "location-selected" },
  },
] as const;

interface LogEntry {
  ts: number;
  source: "iframe-bridge" | "synthetic-button";
  notificationLabel?: string;
  translated: string | null;
}

export default function McpAppsActivePage() {
  const [log, setLog] = useState<LogEntry[]>([]);

  function appendLog(entry: Omit<LogEntry, "ts">) {
    setLog((prev) => [...prev, { ...entry, ts: Date.now() }]);
  }

  function fireSynthetic(n: SyntheticNotification) {
    const translated = notificationToChatMessage(n.payload);
    appendLog({
      source: "synthetic-button",
      notificationLabel: n.label,
      translated,
    });
  }

  function clearLog() {
    setLog([]);
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 p-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">MCP Apps active smoke</h1>
        <p className="text-sm text-muted-foreground">
          Full active iframe → host bridge. The router{"'"}s{" "}
          <code>onChatMessage</code> is wired to the log below instead of
          to <code>useSkillAgent.sendMessage</code> so you can exercise
          the adapter without sending real chat turns.
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Iframe (real bridge)</h2>
        <div className="rounded border p-3">
          <MCPAppToolCallRouter
            toolCalls={[FIXTURE_TOOL_CALL]}
            mcpServerIds={["ext-apps-map"]}
            onChatMessage={(text) =>
              appendLog({ source: "iframe-bridge", translated: text })
            }
          />
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Synthetic notifications</h2>
        <p className="text-xs text-muted-foreground">
          Bypasses the iframe; sends each payload directly to the
          <code>notificationToChatMessage</code> adapter. Useful when the
          iframe isn{"'"}t reachable or you want to verify a specific
          adapter case fast.
        </p>
        <div className="flex flex-wrap gap-2">
          {SYNTHETIC_NOTIFICATIONS.map((n) => (
            <button
              key={n.label}
              type="button"
              onClick={() => fireSynthetic(n)}
              className="rounded border px-3 py-1 text-xs hover:bg-muted"
            >
              fire: {n.label}
            </button>
          ))}
          <button
            type="button"
            onClick={clearLog}
            className="rounded border border-destructive px-3 py-1 text-xs text-destructive hover:bg-destructive/10"
          >
            clear log
          </button>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Adapter log</h2>
        {log.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">
            No notifications yet. Click a button above or interact with
            the iframe to see what the adapter translated.
          </p>
        ) : (
          <ol className="space-y-2 rounded border bg-muted/30 p-3 font-mono text-xs">
            {log.map((entry, i) => (
              <li key={i} className="border-l-2 border-primary pl-2">
                <span className="text-muted-foreground">
                  [{new Date(entry.ts).toLocaleTimeString()}] {entry.source}
                  {entry.notificationLabel ? ` (${entry.notificationLabel})` : ""}{" "}
                  →{" "}
                </span>
                {entry.translated === null ? (
                  <span className="text-muted-foreground">null (adapter ignored)</span>
                ) : (
                  <span className="text-primary">{entry.translated}</span>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}
