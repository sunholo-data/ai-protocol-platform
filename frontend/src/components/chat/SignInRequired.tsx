"use client";

import Link from "next/link";
import { SignInButton } from "@/components/SignInButton";

interface SignInRequiredProps {
  /** Optional skill display name to personalise the headline copy. */
  skillName?: string;
}

/**
 * Sign-in gate panel (v6.4.0 INTERNAL-SHELL M3).
 *
 * Replaces the previous silent `router.replace("/")` on unauthenticated
 * chat URLs. Stays on the chat URL so post-sign-in Firebase auth re-renders
 * directly into the chat the user originally wanted — no need to navigate
 * back from the homepage.
 *
 * Ported from gde-ap-agent app/chat/[...path]/page.tsx lines 524–549.
 */
export function SignInRequired({ skillName }: SignInRequiredProps) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 px-6 text-center">
      <div className="max-w-md space-y-3">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          Sign-in required
        </p>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground md:text-3xl">
          You need to sign in to {skillName ? `open ${skillName}` : "open this chat"}.
        </h1>
        <p className="text-sm text-muted-foreground">
          Sessions, document history, and audit traces are scoped to your
          account. Sign in to continue — you&apos;ll land straight back here.
        </p>
      </div>
      <SignInButton />
      <Link
        href="/"
        className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground"
      >
        ← Back to homepage
      </Link>
    </main>
  );
}
