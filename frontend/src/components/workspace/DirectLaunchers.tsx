"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { type A2uiArtifact, type A2uiV09Message, useSurfaceRegistry } from "@/providers/SurfaceRegistry";

export interface DirectLauncherConfig { label: string; name: string; tool: string }

/** Metadata-driven workbench entry points; never executes arbitrary tools/args. */
export function DirectLaunchers({ skillId, sessionId, launchers }: {
  skillId: string; sessionId: string | null; launchers: readonly DirectLauncherConfig[];
}) {
  const registry = useSurfaceRegistry();
  const [status, setStatus] = useState<{ busy: boolean; error?: boolean; text: string } | null>(null);
  const busy = useRef(false);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    busy.current = false;
    setStatus(null);
    return () => { generation.current += 1; };
  }, [sessionId, skillId]);

  async function launch(entry: DirectLauncherConfig) {
    if (!sessionId || busy.current) return;
    const current = generation.current;
    busy.current = true;
    setStatus({ busy: true, text: `Opening ${entry.label}…` });
    try {
      const response = await fetchWithAuth(
        `/api/proxy/api/skills/${encodeURIComponent(skillId)}/sessions/${encodeURIComponent(sessionId)}/surface-launch-direct`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: entry.name }) },
      );
      const result = await response.json().catch(() => null) as {
        surface_id?: string; messages?: A2uiV09Message[]; artifact?: A2uiArtifact; message?: string; detail?: unknown;
      } | null;
      if (generation.current !== current) return;
      if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : `Could not open the board (HTTP ${response.status}).`);
      if (!result?.surface_id || !Array.isArray(result.messages) || result.messages.length === 0) throw new Error("The board returned no display. Please try again.");
      registry.appendMessages(result.surface_id, result.messages, `launcher:${entry.name}:${Date.now()}`, result.artifact);
      setStatus({ busy: false, text: result.message || "Opened." });
    } catch (error) {
      if (generation.current === current) setStatus({ busy: false, error: true, text: error instanceof Error ? error.message : "Could not reach the server. Please try again." });
    } finally {
      if (generation.current === current) busy.current = false;
    }
  }

  return <div className="space-y-2 rounded-lg border border-border p-3" data-testid="direct-launchers">
    <div className="flex flex-wrap gap-2">{launchers.map(entry => <button key={entry.name} type="button"
      disabled={!sessionId || status?.busy} onClick={() => void launch(entry)}
      className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-60">
      {entry.label}
    </button>)}</div>
    {status && <p role={status.error ? "alert" : "status"} aria-live="polite" className="text-sm text-muted-foreground">{status.text}</p>}
  </div>;
}
