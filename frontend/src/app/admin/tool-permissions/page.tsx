// Tool permissions — the tool-INVOCATION access plane (v6.9.0 / 9.3). CRUD over
// the `tool_permissions` Firestore collection that `adk/callbacks.py` enforces
// per tool call (lookup: user email → domain → wildcard `*`, first match wins;
// `tools` grants — `["*"]` = all — and `denied` revokes and wins). Distinct from
// Group tags (that's skill VISIBILITY, the first plane). The reproducible
// per-env baseline is seeded by backend/scripts/seed_tool_permissions.py; this
// panel is the runtime override. Every write flushes the backend's 60s perm
// cache. Aitana-admin gated by the backend; a 403 renders "admins only".
// Never-silent: loading / error / forbidden / empty / save-notice all render.

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";

type PermType = "user" | "domain" | "wildcard";
type PermDoc = {
  doc_id: string;
  type: PermType;
  tools: string[];
  denied: string[];
};

const API = "/api/proxy/api/admin/tool-permissions";

type Probe = "loading" | "admin" | "forbidden" | "error";

// Split a comma/whitespace/newline separated list into distinct tool names.
// `*` is a legal element (grants/denies all).
function parseList(raw: string): string[] {
  const seen = new Set<string>();
  for (const tok of raw.split(/[\s,]+/)) {
    const t = tok.trim();
    if (t) seen.add(t);
  }
  return [...seen];
}

// Best-effort default `type` from the doc id so a new entry starts sensible;
// the admin can still override it in the select.
function inferType(docId: string): PermType {
  if (docId === "*") return "wildcard";
  if (docId.includes("@")) return "user";
  return "domain";
}

export default function ToolPermissionsPage() {
  const { user, loading } = useAuth();
  const [probe, setProbe] = useState<Probe>("loading");
  const [docs, setDocs] = useState<PermDoc[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  // Upsert form
  const [docId, setDocId] = useState("");
  const [type, setType] = useState<PermType>("domain");
  const [tools, setTools] = useState("");
  const [denied, setDenied] = useState("");
  const [busy, setBusy] = useState(false);

  // Per-row delete
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetchWithAuth(API);
      if (r.status === 403) return setProbe("forbidden");
      if (!r.ok) return setProbe("error");
      const list: PermDoc[] = await r.json();
      // Stable order: wildcard first, then domains, then users — matches the
      // resolution precedence readers reason about.
      const rank = (t: PermType) => (t === "wildcard" ? 0 : t === "domain" ? 1 : 2);
      list.sort((a, b) => rank(a.type) - rank(b.type) || a.doc_id.localeCompare(b.doc_id));
      setDocs(list);
      setProbe("admin");
    } catch {
      setProbe("error");
    }
  };

  useEffect(() => {
    if (loading || !user) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, user]);

  const editRow = (d: PermDoc) => {
    setDocId(d.doc_id);
    setType(d.type);
    setTools(d.tools.join(", "));
    setDenied(d.denied.join(", "));
    setNotice(null);
    if (typeof window !== "undefined") window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  };

  const resetForm = () => {
    setDocId("");
    setType("domain");
    setTools("");
    setDenied("");
  };

  const upsert = async () => {
    const id = docId.trim();
    if (!id) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${API}/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, tools: parseList(tools), denied: parseList(denied) }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setNotice(detail?.detail || `Could not save "${id}".`);
        return;
      }
      setNotice(`Saved "${id}". Permission cache flushed.`);
      resetForm();
      await load();
    } catch {
      setNotice(`Could not save "${id}".`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (typeof window !== "undefined" && !window.confirm(`Delete tool-permission doc "${id}"?`)) return;
    setDeletingId(id);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${API}/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setNotice(detail?.detail || `Could not delete "${id}".`);
        return;
      }
      setNotice(`Deleted "${id}". Permission cache flushed.`);
      await load();
    } catch {
      setNotice(`Could not delete "${id}".`);
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;
  if (probe === "forbidden") {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Admins only</h1>
          <p className="text-sm text-muted-foreground">
            Tool-permission administration requires the <code>aitana-admin</code> group.
          </p>
        </div>
      </Centered>
    );
  }
  if (probe === "error") return <Centered>Something went wrong loading tool permissions.</Centered>;

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Tool permissions</h1>
        <p className="text-sm text-muted-foreground">
          The tool-invocation access plane — which tools a user or domain may call. Resolution is user email → domain →
          wildcard <code>*</code>, first match wins; a <em>domain</em> doc shadows the wildcard, so a grant must be a
          superset of everything that domain touches. <code>tools</code> grants (<code>*</code> = all tools),{" "}
          <code>denied</code> revokes and wins. Distinct from{" "}
          <a className="underline" href="/admin/groups">
            Group tags
          </a>{" "}
          (skill visibility). The reproducible per-env baseline is seeded by{" "}
          <code className="text-xs">seed_tool_permissions.py</code>; edits here are runtime overrides.
        </p>
      </header>

      {/* Docs table */}
      <section className="mb-6 rounded-lg border">
        {docs.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">
            No tool-permission docs yet. Add the first one below (a <code>*</code> wildcard is the usual baseline).
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase text-muted-foreground">
                <th className="px-4 py-2">Id</th>
                <th className="px-4 py-2">Tools</th>
                <th className="px-4 py-2">Denied</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_id} className="border-b last:border-0 align-top">
                  <td className="px-4 py-2">
                    <div className="font-mono text-xs">{d.doc_id}</div>
                    <span className="mt-0.5 inline-block rounded-full border px-2 py-0.5 text-[10px] uppercase text-muted-foreground">
                      {d.type}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    <ToolCells items={d.tools} allLabel="all tools" />
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    <ToolCells items={d.denied} />
                  </td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <button
                      onClick={() => editRow(d)}
                      className="rounded-md border px-2 py-1 text-xs hover:bg-muted/40"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => remove(d.doc_id)}
                      disabled={deletingId === d.doc_id}
                      className="ml-2 rounded-md border px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950/30"
                    >
                      {deletingId === d.doc_id ? "Deleting…" : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Upsert form */}
      <section className="rounded-lg border p-4">
        <h2 className="mb-3 text-sm font-medium">Add / update a doc</h2>
        <div className="grid gap-2">
          <input
            className="rounded-md border px-3 py-2 text-sm font-mono"
            placeholder="id — email, domain (e.g. acmeenergy.com), or * for wildcard"
            value={docId}
            onChange={(e) => {
              setDocId(e.target.value);
              setType(inferType(e.target.value.trim()));
            }}
          />
          <label className="text-xs text-muted-foreground">
            Type
            <select
              className="mt-1 block w-full rounded-md border px-3 py-2 text-sm"
              value={type}
              onChange={(e) => setType(e.target.value as PermType)}
            >
              <option value="wildcard">wildcard</option>
              <option value="domain">domain</option>
              <option value="user">user</option>
            </select>
          </label>
          <input
            className="rounded-md border px-3 py-2 text-sm font-mono"
            placeholder="tools — comma/space separated, or * for all (e.g. ai_search, list_documents)"
            value={tools}
            onChange={(e) => setTools(e.target.value)}
          />
          <input
            className="rounded-md border px-3 py-2 text-sm font-mono"
            placeholder="denied — comma/space separated (wins over tools); leave blank for none"
            value={denied}
            onChange={(e) => setDenied(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <button onClick={resetForm} disabled={busy} className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted/40 disabled:opacity-50">
              Clear
            </button>
            <button
              onClick={upsert}
              disabled={busy || !docId.trim()}
              className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save doc"}
            </button>
          </div>
        </div>
        {notice && <p className="mt-2 text-xs text-muted-foreground">{notice}</p>}
      </section>
    </main>
  );
}

// Render a tools/denied cell: `["*"]` shows as an "all tools" badge; an empty
// list as an em dash; otherwise the names.
function ToolCells({ items, allLabel }: { items: string[]; allLabel?: string }) {
  if (items.length === 0) return <span className="opacity-50">—</span>;
  if (allLabel && items.length === 1 && items[0] === "*") {
    return <span className="rounded-full border px-2 py-0.5 text-[10px] uppercase">{allLabel}</span>;
  }
  return <span className="break-words">{items.join(", ")}</span>;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
