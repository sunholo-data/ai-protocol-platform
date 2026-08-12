"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { useLandingTarget } from "@/hooks/useLandingTarget";

/**
 * v6.5.0 AUTH-LANDING — makes the homepage the logged-out front door.
 *
 * - Logged-out (or auth still hydrating): render the landing (`children`)
 *   unchanged — this is the marketing/marketplace page.
 * - Logged-in: resolve the landing target and redirect into the app — their
 *   last chat, or a fresh primary-skill chat. If there's nothing to route to
 *   (no skills / unconfigured), fall back to rendering the landing.
 *
 * The landing markup is server-rendered and passed in as `children`, so the
 * logged-out experience is byte-for-byte what it was before this gate.
 */
export function HomeGate({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const target = useLandingTarget(!loading && !!user);

  useEffect(() => {
    if (target.kind === "resume" || target.kind === "fresh") {
      router.replace(target.href);
    }
  }, [target, router]);

  // Logged-out, or auth not resolved yet → the landing. (Auth hydration is
  // brief; prioritising the logged-out path avoids a spinner on the public
  // page.)
  if (loading || !user) return <>{children}</>;

  // Logged-in but nothing to route to → the landing is the graceful fallback.
  if (target.kind === "landing") return <>{children}</>;

  // Logged-in and resolving/redirecting → a minimal hold so the marketing
  // page doesn't flash before the redirect lands.
  return (
    <div
      data-testid="home-gate-redirecting"
      className="flex min-h-screen items-center justify-center text-sm text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      <span className="inline-flex items-center gap-2">
        <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
          <path d="M12 2 a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" fill="none" />
        </svg>
        Taking you to your workspace…
      </span>
    </div>
  );
}
