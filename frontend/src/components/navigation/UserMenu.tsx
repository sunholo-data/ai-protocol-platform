"use client";

/**
 * Top-right account area for the SkillsBar.
 *
 * - Signed out → a "Sign in" button (Google popup via AuthContext.signIn).
 * - Signed in  → the user's avatar (photoURL, else an initial chip) that opens
 *   a small menu with their name/email and Sign out.
 *
 * Uses AuthContext, which already abstracts Firebase / anonymous-group / local
 * modes, so this works across all auth modes without branching.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAdminScope } from "@/hooks/useAdminScope";
import { useAuth } from "@/contexts/AuthContext";

export function UserMenu() {
  const { user, loading, signIn, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  // Admin discoverability: the /admin area was URL-only. Ask the backend for
  // the caller's admin scope lazily on first menu-open and reveal an "Admin"
  // link to any admin — platform OR tenant. v6.16.0: this used to probe
  // GET /api/admin/clients (a platform-only DATA endpoint), which is exactly
  // why a tenant-admin holder never saw this link.
  const { isAdmin } = useAdminScope(open);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  if (loading) {
    return <div className="h-8 w-8 shrink-0 animate-pulse rounded-full bg-muted" />;
  }

  if (!user) {
    return (
      <button
        type="button"
        onClick={() => void signIn()}
        className="shrink-0 rounded-md border px-3 py-1.5 text-sm text-foreground hover:bg-muted"
      >
        Sign in
      </button>
    );
  }

  const name = user.displayName || user.email || "Account";
  const initial = (user.displayName || user.email || "?").charAt(0).toUpperCase();

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full border border-border bg-muted text-sm font-medium text-muted-foreground hover:ring-2 hover:ring-primary/30"
      >
        {user.photoURL ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={user.photoURL} alt={name} className="h-full w-full object-cover" />
        ) : (
          <span aria-hidden>{initial}</span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 w-56 rounded-md border bg-background p-1 shadow-md"
        >
          <div className="border-b px-3 py-2">
            <p className="truncate text-sm font-medium text-foreground">
              {user.displayName || "Signed in"}
            </p>
            {user.email && (
              <p className="truncate text-xs text-muted-foreground">{user.email}</p>
            )}
          </div>
          {isAdmin && (
            <Link
              href="/admin"
              role="menuitem"
              onClick={() => setOpen(false)}
              className="mt-1 block w-full rounded-sm px-3 py-2 text-left text-sm text-foreground hover:bg-muted"
            >
              Admin
            </Link>
          )}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className="mt-1 w-full rounded-sm px-3 py-2 text-left text-sm text-foreground hover:bg-muted"
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
