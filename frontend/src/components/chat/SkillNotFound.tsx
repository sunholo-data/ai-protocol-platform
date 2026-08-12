"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";

interface SkillNotFoundProps {
  /** Slug from the URL (e.g. "one-ppa-expert") so the user can see what they tried to open. */
  slug?: string;
}

interface WhoAmI {
  email?: string;
  groupTags?: string[];
}

/**
 * "Skill not found" panel for signed-in users — sibling of SignInRequired
 * for the wrong-account case. The backend's by-slug route deliberately
 * returns 404 for both "missing skill" and "skill exists but caller can't
 * see it" (to avoid leaking existence via slug-guessing — see
 * backend/skills/routes.py:183-198), so the frontend can't distinguish
 * the two. This panel surfaces the caller's identity + group tags so
 * they can tell at a glance whether they're on the right account.
 *
 * Visual language mirrors SignInRequired for consistency: centered
 * column, mono uppercase eyebrow, semibold headline, muted body.
 */
export function SkillNotFound({ slug }: SkillNotFoundProps) {
  const { user, signOut } = useAuth();
  const [identity, setIdentity] = useState<WhoAmI | null>(null);

  useEffect(() => {
    if (!user) return;
    fetchWithAuth("/api/proxy/api/auth/whoami")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setIdentity({ email: data.email, groupTags: data.groupTags ?? [] });
      })
      .catch(() => {
        // Whoami isn't critical — fall back to Firebase-supplied email.
        setIdentity({ email: user.email ?? undefined, groupTags: [] });
      });
  }, [user]);

  const email = identity?.email ?? user?.email ?? "(unknown account)";
  const tags = identity?.groupTags ?? null;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="max-w-lg space-y-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Skill not found
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground md:text-3xl">
          You don&apos;t have access to {slug ? <code className="font-mono text-[0.85em]">{slug}</code> : "this skill"}.
        </h1>
        <p className="text-sm text-muted-foreground">
          Either the URL is wrong, or this skill is restricted to a group
          your account isn&apos;t in. Switch to an account with the right
          access, or ask your admin to add you to the relevant group.
        </p>
        <div className="mt-4 rounded-md border border-border bg-muted/30 p-3 text-left text-xs text-muted-foreground">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider opacity-70">
              Signed in as
            </span>
            <span className="font-mono text-foreground">{email}</span>
          </div>
          {tags !== null && (
            <div className="mt-1 flex items-baseline gap-2">
              <span className="font-mono text-[10px] uppercase tracking-wider opacity-70">
                Groups
              </span>
              {tags.length > 0 ? (
                <span className="font-mono">{tags.join(", ")}</span>
              ) : (
                <span className="italic">(none)</span>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="flex flex-col items-center gap-3">
        <button
          type="button"
          onClick={() => void signOut()}
          className="rounded-md border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted"
        >
          Sign out and switch account
        </button>
        <Link
          href="/"
          className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground"
        >
          ← Back to homepage
        </Link>
      </div>
    </main>
  );
}
