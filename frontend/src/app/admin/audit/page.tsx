// Audit trail — who changed what, and when (v6.16.0 Phase 4).
//
// The trail has been written since v6.9.0 and readable by nobody: records piled
// up in `admin_audit` and were reachable only via raw Firestore. This is the
// read surface, scoped by the backend to the caller's tenants.
//
// Lives under the "Activity & audit" area of the console IA.

"use client";

import { useCallback, useEffect, useState } from "react";

import { SignInRequired } from "@/components/chat/SignInRequired";
import { useAuth } from "@/contexts/AuthContext";
import { useAdminScope } from "@/hooks/useAdminScope";
import { fetchWithAuth } from "@/lib/apiClient";

interface AuditEntry {
  id: string;
  ts: string;
  actorUid: string;
  actorEmail: string;
  action: string;
  target: string;
  before?: unknown;
  after?: unknown;
}

interface AuditResponse {
  entries: AuditEntry[];
  scanned: number;
  scope: string;
}

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: AuditResponse };

function fmt(ts: string): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function AuditPage() {
  const { user, loading } = useAuth();
  const { state: scopeState, isAdmin } = useAdminScope(!loading && !!user);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const r = await fetchWithAuth("/api/proxy/api/admin/audit?limit=200");
      if (!r.ok) {
        setState({
          kind: "error",
          message:
            r.status === 403
              ? "You don't have access to the audit trail."
              : `Couldn't load the audit trail (HTTP ${r.status}).`,
        });
        return;
      }
      setState({ kind: "ready", data: (await r.json()) as AuditResponse });
    } catch {
      setState({ kind: "error", message: "Couldn't reach the admin service — this is a connection problem." });
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  if (loading || scopeState === "loading") return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;
  if (scopeState === "error") {
    return (
      <Centered>
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t check your access. This is a connection problem, not a permissions one.
        </p>
      </Centered>
    );
  }
  if (!isAdmin) {
    return (
      <Centered>
        <p className="text-sm text-muted-foreground">The audit trail requires an admin group.</p>
      </Centered>
    );
  }

  return (
    <main className="mx-auto max-w-5xl p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Audit trail</h1>
        <p className="text-sm text-muted-foreground">Every admin change: who, what, when.</p>
      </header>

      {state.kind === "loading" && <p className="text-sm text-muted-foreground">Loading…</p>}

      {state.kind === "error" && (
        <div className="rounded border border-destructive/40 bg-destructive/5 px-3 py-2">
          <p className="text-sm text-destructive">{state.message}</p>
          <button className="mt-2 rounded border px-2 py-1 text-xs" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}

      {state.kind === "ready" && (
        <>
          {state.data.entries.length === 0 ? (
            // "Nothing has happened" and "nothing here concerns your tenant" are
            // different facts. `scanned` lets us say which, instead of showing an
            // empty table that implies the first.
            <p className="text-sm text-muted-foreground">
              {state.data.scanned > 0
                ? `No audit entries for your tenant. ${state.data.scanned} entries exist platform-wide, outside your scope.`
                : "No admin changes have been recorded yet."}
            </p>
          ) : (
            <>
              <p className="mb-2 text-xs text-muted-foreground">
                Showing {state.data.entries.length}
                {state.data.scope === "tenant" && state.data.scanned > state.data.entries.length && (
                  <> of {state.data.scanned} platform-wide — scoped to your tenant</>
                )}
              </p>
              <div className="overflow-x-auto rounded border">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">When</th>
                      <th className="px-3 py-2">Who</th>
                      <th className="px-3 py-2">Action</th>
                      <th className="px-3 py-2">Target</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {state.data.entries.map((e) => (
                      <tr key={e.id} className="border-t align-top">
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">{fmt(e.ts)}</td>
                        <td className="px-3 py-2 text-xs">{e.actorEmail || e.actorUid || "—"}</td>
                        <td className="px-3 py-2">
                          <code className="text-xs">{e.action}</code>
                        </td>
                        <td className="px-3 py-2 text-xs">{e.target || "—"}</td>
                        <td className="px-3 py-2 text-right">
                          {(e.before !== null || e.after !== null) && (
                            <button
                              className="text-xs underline"
                              onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                            >
                              {expanded === e.id ? "Hide" : "Diff"}
                            </button>
                          )}
                          {expanded === e.id && (
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-left text-[11px]">
                              {JSON.stringify({ before: e.before, after: e.after }, null, 2)}
                            </pre>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
