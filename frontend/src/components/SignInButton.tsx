"use client";

import { useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { cn } from "@/lib/utils";

/**
 * Google Sign-In button. When signed out, clicking opens a Google popup;
 * if the popup flow fails (Safari blocks third-party storage that the popup
 * relies on in some configurations), we fall back to a full-page redirect.
 * When signed in, it becomes a Sign-Out button with the user's email next to
 * it.
 */
export function SignInButton() {
  const { user, loading, signIn, signInWithRedirect, signOut } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSignIn = async () => {
    setBusy(true);
    setError(null);
    try {
      await signIn();
    } catch (popupErr) {
      // Safari popup-blocked or third-party-storage fail — fall back to redirect.
      console.warn("popup sign-in failed, falling back to redirect", popupErr);
      try {
        await signInWithRedirect();
      } catch (redirectErr) {
        setError(String(redirectErr));
      }
    } finally {
      setBusy(false);
    }
  };

  const handleSignOut = async () => {
    setBusy(true);
    setError(null);
    try {
      await signOut();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <span
        className="text-xs text-muted-foreground"
        data-testid="sign-in-loading"
      >
        checking auth…
      </span>
    );
  }

  if (user) {
    return (
      <div className="flex items-center gap-3" data-testid="signed-in">
        <span className="text-sm text-muted-foreground">{user.email}</span>
        <button
          type="button"
          onClick={handleSignOut}
          disabled={busy}
          className={cn(
            "rounded-md border border-input bg-background px-3 py-1.5 text-sm",
            "hover:bg-muted disabled:opacity-50",
          )}
        >
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        onClick={handleSignIn}
        disabled={busy}
        data-testid="sign-in-button"
        className={cn(
          "inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm",
          "font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50",
        )}
      >
        {busy ? "Signing in…" : "Sign in with Google"}
      </button>
      {error && (
        <span
          className="text-xs text-red-600"
          data-testid="sign-in-error"
          role="alert"
        >
          {error}
        </span>
      )}
    </div>
  );
}
