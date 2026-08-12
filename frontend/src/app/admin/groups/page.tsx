// Group tags — the registry admin (v6.9.0 / 9.3). Makes a group tag first-class:
// label / description / what-it-grants. The registry is the vocabulary that
// per-user grant validation checks against (an unknown tag → 422). Also answers
// "who holds tag X" (reverse lookup). Aitana-admin gated by the backend; a 403
// renders the "admins only" state. Never-silent: loading / error / empty render.

"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";

type GroupTag = {
  id: string;
  label?: string;
  description?: string;
  grants?: string[];
  tenant_scope?: string | null;
};
type TagMember = { email: string; uid: string };
type TagMembers = { tag: string; members: TagMember[]; scanned: number; truncated: boolean; note: string };

const REGISTRY = "/api/proxy/api/admin/group-tags";
const MEMBERS = "/api/proxy/api/admin/groups";

type Probe = "loading" | "admin" | "forbidden" | "error";

export default function GroupsPage() {
  const { user, loading } = useAuth();
  const [probe, setProbe] = useState<Probe>("loading");
  const [tags, setTags] = useState<GroupTag[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  // Upsert form
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [grants, setGrants] = useState("");
  const [busy, setBusy] = useState(false);

  // Members lookup
  const [membersOf, setMembersOf] = useState<TagMembers | null>(null);
  const [membersBusy, setMembersBusy] = useState(false);

  const load = async () => {
    try {
      const r = await fetchWithAuth(REGISTRY);
      if (r.status === 403) return setProbe("forbidden");
      if (!r.ok) return setProbe("error");
      setTags(await r.json());
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

  const upsert = async () => {
    const tagId = id.trim();
    if (!tagId) return;
    setBusy(true);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${REGISTRY}/${encodeURIComponent(tagId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          label: label.trim(),
          description: description.trim(),
          grants: grants
            .split(",")
            .map((g) => g.trim())
            .filter(Boolean),
        }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setNotice(detail?.detail || `Could not save "${tagId}".`);
        return;
      }
      setNotice(`Saved "${tagId}".`);
      setId("");
      setLabel("");
      setDescription("");
      setGrants("");
      await load();
    } catch {
      setNotice(`Could not save "${tagId}".`);
    } finally {
      setBusy(false);
    }
  };

  const lookupMembers = async (tag: string) => {
    setMembersBusy(true);
    setMembersOf(null);
    setNotice(null);
    try {
      const r = await fetchWithAuth(`${MEMBERS}/${encodeURIComponent(tag)}/members`);
      if (!r.ok) {
        setNotice(`Could not look up holders of "${tag}".`);
        return;
      }
      setMembersOf(await r.json());
    } catch {
      setNotice(`Could not look up holders of "${tag}".`);
    } finally {
      setMembersBusy(false);
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
            Group-tag administration requires the <code>aitana-admin</code> group.
          </p>
        </div>
      </Centered>
    );
  }
  if (probe === "error") return <Centered>Something went wrong loading the registry.</Centered>;

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Group tags</h1>
        <p className="text-sm text-muted-foreground">
          The tag registry — what each tag grants, and who holds it. Per-user grants are set under{" "}
          <a className="underline" href="/admin/users">
            Users
          </a>
          ; a grant of an unregistered tag is rejected.
        </p>
      </header>

      {/* Registry table */}
      <section className="mb-6 rounded-lg border">
        {tags.length === 0 ? (
          <p className="px-4 py-6 text-center text-sm text-muted-foreground">
            No tags registered yet. Add the first one below.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs uppercase text-muted-foreground">
                <th className="px-4 py-2">Tag</th>
                <th className="px-4 py-2">Grants</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {tags.map((t) => (
                <tr key={t.id} className="border-b last:border-0">
                  <td className="px-4 py-2 align-top">
                    <div className="font-mono text-xs">{t.id}</div>
                    {t.label && <div className="text-xs text-muted-foreground">{t.label}</div>}
                    {t.description && <div className="mt-0.5 text-xs text-muted-foreground">{t.description}</div>}
                  </td>
                  <td className="px-4 py-2 align-top text-xs text-muted-foreground">
                    {t.grants?.length ? t.grants.join(", ") : <span className="opacity-50">—</span>}
                  </td>
                  <td className="px-4 py-2 align-top text-right">
                    <button
                      onClick={() => lookupMembers(t.id)}
                      disabled={membersBusy}
                      className="rounded-md border px-2 py-1 text-xs hover:bg-muted/40 disabled:opacity-50"
                    >
                      Holders
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Members panel */}
      {membersBusy && <Note>Scanning holders…</Note>}
      {membersOf && (
        <section className="mb-6 rounded-lg border p-4">
          <h2 className="mb-2 text-sm font-medium">
            Holders of <code className="font-mono">{membersOf.tag}</code>
          </h2>
          {membersOf.members.length === 0 ? (
            <p className="text-sm text-muted-foreground">No direct holders.</p>
          ) : (
            <ul className="flex flex-wrap gap-2">
              {membersOf.members.map((m) => (
                <li key={m.uid} className="rounded-full border px-2 py-0.5 text-xs">
                  {m.email}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            {membersOf.note}
            {membersOf.truncated && " — list truncated; refine or raise the scan cap."}
          </p>
        </section>
      )}

      {/* Upsert form */}
      <section className="rounded-lg border p-4">
        <h2 className="mb-3 text-sm font-medium">Add / update a tag</h2>
        <div className="grid gap-2">
          <input
            className="rounded-md border px-3 py-2 text-sm"
            placeholder="tag id (e.g. ONE, beta-tester)"
            value={id}
            onChange={(e) => setId(e.target.value)}
          />
          <input
            className="rounded-md border px-3 py-2 text-sm"
            placeholder="label (human name)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <input
            className="rounded-md border px-3 py-2 text-sm"
            placeholder="description — what holding this tag grants"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <input
            className="rounded-md border px-3 py-2 text-sm"
            placeholder="grants (comma-separated skill slugs / capabilities)"
            value={grants}
            onChange={(e) => setGrants(e.target.value)}
          />
          <div className="flex justify-end">
            <button
              onClick={upsert}
              disabled={busy || !id.trim()}
              className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save tag"}
            </button>
          </div>
        </div>
        {notice && <p className="mt-2 text-xs text-muted-foreground">{notice}</p>}
      </section>
    </main>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <div className="mb-6 rounded-lg border px-3 py-6 text-center text-sm text-muted-foreground">{children}</div>;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
