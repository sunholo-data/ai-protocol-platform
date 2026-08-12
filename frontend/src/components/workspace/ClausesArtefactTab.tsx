// ClausesArtefactTab — the workbench "Clauses" Result tab (6.11).
//
// PPA clause extraction renders as a professional table. The Basic A2UI catalog
// has no Table and limited styling, so the backend transform
// (adk/a2ui_ppa_render.py) stashes the structured clauses in the surface data
// model at "/"; this tab reads that and renders a real aligned table with
// colour-coded confidence badges (the component tree is the generic fallback).

"use client";

import { useSurfaceState } from "@/providers/SurfaceRegistry";

interface Clause {
  name?: string;
  value?: string;
  confidence?: string;
}

function confidenceStyle(c: string): string {
  const v = c.toLowerCase();
  if (v === "high") return "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300";
  if (v === "medium" || v === "med") return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  if (v === "low") return "bg-rose-500/12 text-rose-700 dark:text-rose-300";
  return "bg-muted text-muted-foreground";
}

export function ClausesArtefactTab({ surfaceId, className }: { surfaceId: string; className?: string }) {
  const state = useSurfaceState(surfaceId);
  const root = (state?.surface?.dataModel?.get("/") ?? null) as
    | { clauses?: Clause[]; docName?: string; truncatedTotal?: number | null }
    | null;
  const clauses = (root?.clauses ?? []).filter((c) => c.name);

  return (
    <div className={`flex h-full flex-col overflow-auto ${className ?? ""}`}>
      <div className="sticky top-0 z-10 border-b border-border bg-background/95 px-4 py-3 backdrop-blur">
        <h2 className="text-sm font-semibold tracking-tight text-foreground">Extracted clauses</h2>
        {root?.docName && <p className="truncate text-xs text-muted-foreground">{root.docName}</p>}
      </div>

      {clauses.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No clauses were extracted from this contract.</p>
      ) : (
        <table className="w-full border-collapse text-sm">
          <tbody>
            {clauses.map((c, i) => (
              <tr key={`${c.name}-${i}`} className={i % 2 ? "bg-muted/20" : undefined}>
                <th
                  scope="row"
                  className="w-[38%] whitespace-normal border-b border-border/60 px-4 py-2.5 text-left align-top font-medium text-foreground"
                >
                  {c.name}
                </th>
                <td className="border-b border-border/60 px-4 py-2.5 align-top text-foreground">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{c.value || "—"}</span>
                    {c.confidence && (
                      <span
                        className={`mt-0.5 shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${confidenceStyle(
                          c.confidence,
                        )}`}
                      >
                        {c.confidence}
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {root?.truncatedTotal ? (
        <p className="px-4 py-2 text-xs text-muted-foreground">
          Showing {clauses.length} of {root.truncatedTotal} clauses.
        </p>
      ) : null}
    </div>
  );
}
