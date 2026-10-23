"use client";

// Personal MCP tokens — connect an MCP client (Claude Code, Claude Desktop, …)
// to the skills you can use here, as you.
//
// The token is shown ONCE (only its hash is stored), with a ready-to-paste
// client command. A token opens the MCP endpoint only — never the rest of the
// API. Every state renders something (never-silent): loading, signed out, the
// list, the new token, and errors.

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";

const API = "/api/proxy/api/mcp/tokens";

type Token = { id: string; name: string; createdAt: string | null; expiresAt: string | null; lastUsedAt: string | null };
type Created = Token & { token: string };

function day(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export default function McpTokensPage() {
  const { user, loading } = useAuth();
  const [tokens, setTokens] = useState<Token[] | null>(null);
  const [name, setName] = useState("Claude Code");
  const [created, setCreated] = useState<Created | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetchWithAuth(API);
      if (!r.ok) throw new Error(String(r.status));
      setTokens(await r.json());
    } catch {
      setError("Couldn't load your MCP tokens. Refresh to try again.");
    }
  }, []);

  useEffect(() => {
    if (!loading && user) void load();
  }, [loading, user, load]);

  const create = async () => {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const r = await fetchWithAuth(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "");
      setCreated(body as Created);
      await load();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : "Couldn't create a token. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fetchWithAuth(`${API}/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(String(r.status));
      if (created?.id === id) setCreated(null);
      await load();
    } catch {
      setError("Couldn't revoke that token. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <Page>Loading…</Page>;
  if (!user) return <SignInRequired />;

  const endpoint = `${typeof window === "undefined" ? "" : window.location.origin}/api/proxy/mcp`;
  const command = created
    ? `claude mcp add --transport http ${slug(typeof document === "undefined" ? "" : document.title)} ${endpoint} --header "Authorization: Bearer ${created.token}"`
    : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setError("Couldn't copy — select the command and copy it by hand.");
    }
  };

  return (
    <Page>
      <div className="w-full max-w-2xl space-y-6">
        <div className="space-y-2">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">MCP</p>
          <h1 className="text-2xl font-semibold">Use your skills from an MCP client</h1>
          <p className="text-sm text-muted-foreground">
            A personal token lets Claude Code, Claude Desktop or another MCP client call the skills you can use
            here, as you. It works only for MCP, and lasts 90 days. Treat it like a password.
          </p>
        </div>

        {created ? (
          <div data-testid="new-token" className="space-y-3 border border-border p-4">
            <p className="text-sm font-medium">Your new token — copy it now, it won&apos;t be shown again.</p>
            <p className="text-sm">For Claude Code, run:</p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all bg-muted p-3 font-mono text-xs">{command}</pre>
            <p className="text-xs text-muted-foreground">
              Other clients: streamable HTTP at <span className="font-mono">{endpoint}</span> with the header{" "}
              <span className="font-mono">Authorization: Bearer &lt;token&gt;</span>.
            </p>
            <button
              type="button"
              onClick={() => void copy()}
              className="border border-border px-3 py-1.5 text-sm hover:bg-muted"
            >
              {copied ? "Copied" : "Copy command"}
            </button>
          </div>
        ) : null}

        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Token name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={60}
              className="border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => void create()}
            disabled={busy}
            className="border border-border px-4 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            {busy ? "Working…" : "Create token"}
          </button>
        </div>

        <div className="space-y-2">
          <h2 className="text-sm font-medium">Your tokens</h2>
          {tokens === null && !error ? <p className="text-sm">Loading…</p> : null}
          {tokens && tokens.length === 0 ? <p className="text-sm text-muted-foreground">None yet.</p> : null}
          {tokens && tokens.length > 0 ? (
            <ul data-testid="token-list" className="divide-y divide-border border border-border">
              {tokens.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span>
                    <strong>{t.name}</strong>{" "}
                    <span className="text-muted-foreground">
                      · expires {day(t.expiresAt)} · last used {day(t.lastUsedAt)}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void revoke(t.id)}
                    disabled={busy}
                    className="text-muted-foreground hover:underline disabled:opacity-50"
                  >
                    Revoke
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>
    </Page>
  );
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "platform";
}

function Page({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center px-6 py-10">{children}</main>;
}
