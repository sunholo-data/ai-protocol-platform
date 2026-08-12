// Users & Groups — admin per-user group-tag editor (v6.9.0 user-group-administration).
// Look up a user by email, see their per-user group tags, and grant/revoke tags
// (writes the Firebase groupTags custom claim; the change takes effect on the
// user's next token refresh). Aitana-admin gated by the backend; a 403 renders
// the "admins only" state. Never-silent: loading / error / empty paths render.
//
// Domain-wide tags (whole email domain) are managed under /admin/tenants
// (derived_group_tags); this page is for per-user grants.

"use client";

import { useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";

type UserGroups = { email: string; uid: string; group_tags: string[] };
type ProvenancedTag = { tag: string; provenances: string[] };
type AccessCheck = { tags: ProvenancedTag[] };

const API = "/api/proxy/api/admin/users";
const ACCESS = "/api/proxy/api/admin/access/check";

type Status = "idle" | "loading" | "ok" | "notfound" | "forbidden" | "error";

export default function UsersPage() {
  const { user, loading } = useAuth();
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [data, setData] = useState<UserGroups | null>(null);
  const [newTag, setNewTag] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [access, setAccess] = useState<AccessCheck | null>(null);
  const [accessBusy, setAccessBusy] = useState(false);

  const lookup = async (addr: string) => {
    const e = addr.trim();
    if (!e) return;
    setStatus("loading");
    setData(null);
    setNotice(null);
    setAccess(null);
    try {
      const r = await fetchWithAuth(`${API}/${encodeURIComponent(e)}`);
      if (r.status === 403) return setStatus("forbidden");
      if (r.status === 404) return setStatus("notfound");
      if (!r.ok) return setStatus("error");
      setData(await r.json());
      setStatus("ok");
    } catch {
      setStatus("error");
    }
  };

  const grant = async () => {
    if (!data) return;
    const tag = newTag.trim();
    if (!tag) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${API}/${encodeURIComponent(data.email)}/groups`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag }),
      });
      if (!r.ok) {
        // Surface the backend's reason (e.g. 422 unknown-tag from the registry)
        // rather than a generic failure (NEVER-SILENT).
        const detail = await r.json().catch(() => null);
        setNotice(detail?.detail || `Could not grant "${tag}".`);
        return;
      }
      setData(await r.json());
      setNewTag("");
      setNotice(`Granted "${tag}". Takes effect on the user's next sign-in / token refresh.`);
    } catch {
      setNotice(`Could not grant "${tag}".`);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (tag: string) => {
    if (!data) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetchWithAuth(
        `${API}/${encodeURIComponent(data.email)}/groups/${encodeURIComponent(tag)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        setNotice(`Could not revoke "${tag}".`);
        return;
      }
      setData(await r.json());
      setNotice(`Revoked "${tag}". Takes effect on the user's next sign-in / token refresh.`);
    } catch {
      setNotice(`Could not revoke "${tag}".`);
    } finally {
      setBusy(false);
    }
  };

  const checkAccess = async () => {
    if (!data) return;
    setAccessBusy(true);
    setAccess(null);
    try {
      const r = await fetchWithAuth(ACCESS, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: data.email }),
      });
      if (!r.ok) {
        setNotice("Could not compute effective access.");
        return;
      }
      setAccess(await r.json());
    } catch {
      setNotice("Could not compute effective access.");
    } finally {
      setAccessBusy(false);
    }
  };

  if (loading) return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;
  if (status === "forbidden") {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Admins only</h1>
          <p className="text-sm text-muted-foreground">
            User administration requires the <code>aitana-admin</code> group.
          </p>
        </div>
      </Centered>
    );
  }

  return (
    <main className="mx-auto max-w-2xl p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Users &amp; Groups</h1>
        <p className="text-sm text-muted-foreground">
          Grant or revoke a user&apos;s group tags. Domain-wide tags are set under{" "}
          <a className="underline" href="/admin/tenants">
            Tenants
          </a>
          .
        </p>
      </header>

      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          lookup(email);
        }}
      >
        <input
          type="email"
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          placeholder="user@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit" className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40">
          Look up
        </button>
      </form>

      {status === "loading" && <Note>Looking up user…</Note>}
      {status === "notfound" && <Note>No user found for that email.</Note>}
      {status === "error" && <Note>Something went wrong. Try again.</Note>}

      {status === "ok" && data && (
        <section className="rounded-lg border p-4">
          <div className="mb-1 text-sm font-medium">{data.email}</div>
          <div className="mb-3 font-mono text-xs text-muted-foreground">{data.uid}</div>

          <h2 className="mb-1 text-xs font-medium uppercase text-muted-foreground">Group tags</h2>
          {data.group_tags.length === 0 ? (
            <p className="mb-3 text-sm text-muted-foreground">No per-user tags.</p>
          ) : (
            <ul className="mb-3 flex flex-wrap gap-2">
              {data.group_tags.map((t) => (
                <li
                  key={t}
                  className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
                >
                  <span>{t}</span>
                  <button
                    aria-label={`Revoke ${t}`}
                    disabled={busy}
                    onClick={() => revoke(t)}
                    className="text-muted-foreground hover:text-red-600 disabled:opacity-50"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="flex gap-2">
            <input
              className="flex-1 rounded-md border px-3 py-2 text-sm"
              placeholder="Add a tag (e.g. ONE, aitana-admin)"
              value={newTag}
              disabled={busy}
              onChange={(e) => setNewTag(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  grant();
                }
              }}
            />
            <button
              onClick={grant}
              disabled={busy || !newTag.trim()}
              className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50"
            >
              {busy ? "Saving…" : "Grant"}
            </button>
          </div>

          {notice && <p className="mt-2 text-xs text-muted-foreground">{notice}</p>}

          <div className="mt-4 border-t pt-3">
            <button
              onClick={checkAccess}
              disabled={accessBusy}
              className="rounded-md border px-3 py-1.5 text-xs hover:bg-muted/40 disabled:opacity-50"
            >
              {accessBusy ? "Computing…" : "Show effective access"}
            </button>
            {access && (
              <div className="mt-3">
                <h2 className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                  Effective tags (direct + domain-derived)
                </h2>
                {access.tags.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No effective tags.</p>
                ) : (
                  <ul className="flex flex-wrap gap-2">
                    {access.tags.map((t) => (
                      <li key={t.tag} className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs">
                        <span className="font-mono">{t.tag}</span>
                        {t.provenances.map((p) => (
                          <span
                            key={p}
                            className="rounded-sm bg-muted px-1 text-[10px] uppercase text-muted-foreground"
                          >
                            {p}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </section>
      )}
    </main>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border px-3 py-6 text-center text-sm text-muted-foreground">{children}</div>;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
