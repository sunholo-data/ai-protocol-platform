"use client";

import { useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { cn } from "@/lib/utils";

/**
 * Dev-only probe: hit `GET /api/proxy/api/skills` with the user's Bearer
 * token and show the count. Validates the full round-trip end-to-end:
 *   browser → /api/proxy/[...path] catch-all → sidecar backend →
 *   Depends(get_current_user) → list_skills → access-filtered payload.
 *
 * Only rendered when `NEXT_PUBLIC_SHOW_DEV_PROBES === "true"`, so this does
 * not leak into a production UI.
 */
export function MySkillsButton() {
  const { user } = useAuth();
  const [status, setStatus] = useState<string>("idle");
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    setBusy(true);
    setStatus("requesting…");
    try {
      const resp = await fetchWithAuth("/api/proxy/api/skills");
      if (resp.ok) {
        const data = (await resp.json()) as unknown[];
        setStatus(`${resp.status} OK — ${data.length} skill(s) visible`);
      } else {
        const text = await resp.text();
        setStatus(`${resp.status} — ${text.slice(0, 120)}`);
      }
    } catch (err) {
      setStatus(`error: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-center gap-2" data-testid="dev-probe">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className={cn(
          "rounded-md border border-input bg-background px-3 py-1.5 text-xs",
          "text-muted-foreground hover:bg-muted disabled:opacity-50",
        )}
      >
        {user ? "list my skills" : "try /api/skills (no token)"}
      </button>
      <span
        className="text-xs text-muted-foreground font-mono"
        data-testid="dev-probe-status"
      >
        {status}
      </span>
    </div>
  );
}
