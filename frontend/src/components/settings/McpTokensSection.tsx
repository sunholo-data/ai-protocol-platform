"use client";

// Personal MCP tokens — connect an MCP client (Claude Code, Claude Desktop, …)
// to the skills you can use here, as you.
//
// The token is shown ONCE (only its hash is stored), with a ready-to-paste
// client command. A token opens the MCP endpoint only — never the rest of the
// API. Every state renders something (never-silent): loading, signed out, the
// list, the new token, and errors.
//
// "Can approve actions" adds the right to approve ROUTINE human-approval
// requests from that client or the aiplatform CLI (docs/design/v6.41.0/
// human-approvals.md). Off by default, explained before minting, shorter-lived
// (30 days), marked in the list, and each use is pushed to the owner's phone.

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { useConfirm } from "@/components/ui/confirm";
import { Button } from "@/components/ui/button";
import { useFormat, useT, type Formatter } from "@/i18n";

const API = "/api/proxy/api/mcp/tokens";

type Token = {
  id: string;
  name: string;
  createdAt: string | null;
  expiresAt: string | null;
  lastUsedAt: string | null;
  scopes?: string[];
};
type Created = Token & { token: string };

function day(iso: string | null, never: string, fmt: Formatter): string {
  if (!iso) return never;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : fmt.date(d, { day: "numeric", month: "short", year: "numeric" });
}

/** Settings → Connections. Rendered only for a signed-in user. */
export function McpTokensSection() {
  const t = useT("McpTokensSection");
  const fmt = useFormat();
  const [tokens, setTokens] = useState<Token[] | null>(null);
  const [name, setName] = useState(() => t("defaultName"));
  const [approve, setApprove] = useState(false);
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
      setError(t("loadFailed"));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const r = await fetchWithAuth(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, approve }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "");
      setCreated(body as Created);
      await load();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t("createFailed"));
    } finally {
      setBusy(false);
    }
  };

  const confirm = useConfirm();
  const revoke = async (id: string) => {
    const ok = await confirm({
      title: t("revokeTitle"),
      description: t("revokeDescription"),
      confirmLabel: t("revokeConfirm"),
      destructive: true,
    });
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetchWithAuth(`${API}/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(String(r.status));
      if (created?.id === id) setCreated(null);
      await load();
    } catch {
      setError(t("revokeFailed"));
    } finally {
      setBusy(false);
    }
  };

  const endpoint = `${typeof window === "undefined" ? "" : window.location.origin}/api/proxy/mcp`;
  const command = created
    ? `claude mcp add --transport http ${slug(typeof document === "undefined" ? "" : document.title)} ${endpoint} --header "Authorization: Bearer ${created.token}"`
    : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setError(t("copyFailed"));
    }
  };

  return (
    <section id="connections" aria-labelledby="connections-title" className="scroll-mt-6 space-y-4">
      <div>
        <h2 id="connections-title" className="text-lg font-semibold text-foreground">
          {t("title")}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{t("body")}</p>
      </div>

        {created ? (
          <div data-testid="new-token" className="space-y-3 border border-border p-4">
            <p className="text-sm font-medium">{t("newToken")}</p>
            {canApprove(created) ? (
              <p className="text-sm">{t("newTokenCanApprove")}</p>
            ) : null}
            <p className="text-sm">{t("forClaudeCode")}</p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-all bg-muted p-3 font-mono text-xs">{command}</pre>
            <p className="text-xs text-muted-foreground">
              {t.rich("otherClients", {
                endpoint,
                header: "Authorization: Bearer <token>",
                mono: (chunks) => <span className="font-mono">{chunks}</span>,
              })}
            </p>
            <div className="flex items-center gap-3">
              <Button variant="secondary" size="sm" onClick={() => void copy()}>
                {t("copyCommand")}
              </Button>
              <span aria-live="polite" className="text-xs text-success">
                {copied ? t("copied") : ""}
              </span>
            </div>
          </div>
        ) : null}

        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            {t("tokenName")}
            <input
              name="token-name"
              autoComplete="off"
              spellCheck={false}
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={60}
              className="rounded-[var(--radius)] border border-border bg-background px-3 py-2 text-sm"
            />
          </label>
          <Button type="submit" variant="secondary" pending={busy} pendingLabel={t("creating")}>
            {t("create")}
          </Button>
          <label className="flex basis-full items-center gap-2 text-sm">
            <input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} />
            {t("canApprove")}
          </label>
          {approve ? (
            <p data-testid="approve-warning" className="basis-full text-xs text-muted-foreground">
              {t("approveWarning")}
            </p>
          ) : null}
        </form>

        <div className="space-y-2">
          <h3 className="text-sm font-medium">{t("yourTokens")}</h3>
          {tokens === null && !error ? <p className="text-sm text-muted-foreground">{t("loading")}</p> : null}
          {tokens && tokens.length === 0 ? <p className="text-sm text-muted-foreground">{t("none")}</p> : null}
          {tokens && tokens.length > 0 ? (
            <ul data-testid="token-list" className="divide-y divide-border border border-border">
              {tokens.map((tok) => (
                <li key={tok.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span>
                    <strong>{tok.name}</strong>{" "}
                    {canApprove(tok) ? <span className="border border-border px-1.5 text-xs">{t("canApproveBadge")}</span> : null}{" "}
                    <span className="text-muted-foreground">
                      {t("tokenMeta", { expires: day(tok.expiresAt, t("never"), fmt), lastUsed: day(tok.lastUsedAt, t("never"), fmt) })}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void revoke(tok.id)}
                    disabled={busy}
                    aria-label={t("revokeLabel", { name: tok.name })}
                    className="text-muted-foreground hover:text-destructive hover:underline disabled:opacity-50"
                  >
                    {t("revoke")}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {error ? (
          <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
            <span>{error}</span>
            {tokens === null ? (
              <Button variant="secondary" size="sm" onClick={() => void load()}>
                {t("tryAgain")}
              </Button>
            ) : null}
          </div>
        ) : null}
    </section>
  );
}

function canApprove(token: Token): boolean {
  return Boolean(token.scopes?.includes("approve"));
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "platform";
}
