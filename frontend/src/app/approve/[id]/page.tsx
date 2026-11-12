"use client";

// One human-approval request, decided outside the chat: the link an MCP client
// opens (URL elicitation), the CLI prints, or a notification carries.
//
// The page shows the server-written review and sends back the digest of exactly
// what it showed (docs/design/v6.41.0/human-approvals.md). The link itself
// grants nothing: you must be signed in, and the server answers 404 unless the
// request is yours. High-risk requests need a recent sign-in; the page offers
// one and retries. Every state renders something (never-silent).

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { PageLoading } from "@/components/ui/skeleton";

type Approval = {
  id: string;
  title: string;
  review: string;
  risk: "routine" | "high";
  digest: string;
  expires_at: number;
  state: string;
};
type Decision = "approve" | "cancel";

export default function ApprovePage() {
  const { user, loading, signIn } = useAuth();
  const raw = useParams<{ id: string }>()?.id ?? "";
  const path = `/api/proxy/api/approvals/${encodeURIComponent(decodeURIComponent(raw))}`;
  const [approval, setApproval] = useState<Approval | null>(null);
  const [missing, setMissing] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [needsSignIn, setNeedsSignIn] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetchWithAuth(path);
      if (r.status === 404) return setMissing(true);
      if (!r.ok) throw new Error(String(r.status));
      setApproval(await r.json());
    } catch {
      setError("Couldn't load this approval. Refresh to try again.");
    }
  }, [path]);

  useEffect(() => {
    if (!loading && user) void load();
  }, [loading, user, load]);

  const decide = async (decision: Decision) => {
    if (!approval) return;
    setBusy(true);
    setError(null);
    setNeedsSignIn(false);
    try {
      const r = await fetchWithAuth(`${path}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, digest: approval.digest }),
      });
      const body = await r.json().catch(() => ({}));
      const detail = typeof body.detail === "string" ? body.detail : "";
      if (r.ok) {
        setReceipt(body.message || (decision === "approve" ? "Approved." : "Declined."));
      } else if (r.status === 409) {
        setError("This approval changed after you opened it. Check the new details before deciding.");
        await load();
      } else if (r.status === 403 && /sign-in|sign in/i.test(detail)) {
        setNeedsSignIn(true);
        setError(detail);
      } else {
        setError(detail || "Couldn't record your decision. Please try again.");
      }
    } catch {
      setError("Couldn't record your decision. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const signInAndApprove = async () => {
    await signIn();
    await decide("approve");
  };

  if (loading) return <PageLoading />;
  if (!user) return <SignInRequired />;
  if (missing) {
    return (
      <Page>
        <p className="text-sm">There is no approval here for you. It may belong to someone else, or the link is wrong.</p>
      </Page>
    );
  }
  if (!approval) return <Page>{error ? <Alert>{error}</Alert> : "Loading…"}</Page>;

  const pending = approval.state === "pending" && !receipt;
  return (
    <Page>
      <div className="w-full max-w-2xl space-y-5">
        <div className="space-y-2">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">Approval</p>
          <h1 className="text-2xl font-semibold">{approval.title}</h1>
          {approval.risk === "high" ? (
            <p className="text-sm">This is a high-risk action: approving it needs a recent sign-in.</p>
          ) : null}
        </div>
        <pre
          data-testid="approval-review"
          className="whitespace-pre-wrap break-words border border-border bg-muted/40 p-4 font-sans text-sm"
        >
          {approval.review}
        </pre>
        <p className="font-mono text-xs text-muted-foreground">
          {approval.id} · digest {approval.digest.slice(0, 12)}
        </p>
        {receipt ? <p className="text-sm font-medium">{receipt}</p> : null}
        {!pending && !receipt ? (
          <p className="text-sm">This approval is already {approval.state}. Nothing more to do.</p>
        ) : null}
        {pending ? (
          <div className="flex flex-wrap gap-3">
            {needsSignIn ? (
              <button
                type="button"
                onClick={() => void signInAndApprove()}
                disabled={busy}
                className="bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
              >
                Sign in again and approve
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void decide("approve")}
                disabled={busy}
                className="bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
              >
                Approve
              </button>
            )}
            <button
              type="button"
              onClick={() => void decide("cancel")}
              disabled={busy}
              className="border border-border px-4 py-2 text-sm hover:bg-muted disabled:opacity-50"
            >
              Decline
            </button>
          </div>
        ) : null}
        {error ? <Alert>{error}</Alert> : null}
      </div>
    </Page>
  );
}

function Alert({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="text-sm text-destructive">
      {children}
    </p>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center px-6 py-10">{children}</main>;
}
