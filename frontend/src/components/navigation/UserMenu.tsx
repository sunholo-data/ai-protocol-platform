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

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useAdminScope } from "@/hooks/useAdminScope";
import { useAuth } from "@/contexts/AuthContext";
import { headerControl } from "./headerControls";
import { CircleHelp, FolderKanban, LogOut, Settings, Shield } from "lucide-react";

// Short on purpose: Settings holds instructions, memory, voice, notifications
// and connections; skill actions are in the skill picker.
const LINKS: { href: string; label: string; icon: ReactNode; admin?: boolean }[] = [
  { href: "/settings", label: "Settings", icon: <Settings className="h-4 w-4" /> },
  { href: "/projects", label: "Projects", icon: <FolderKanban className="h-4 w-4" /> },
  { href: "/help", label: "Help", icon: <CircleHelp className="h-4 w-4" /> },
  { href: "/admin", label: "Admin", icon: <Shield className="h-4 w-4" />, admin: true },
];

const ROW =
  "flex min-h-[44px] w-full items-center gap-3 rounded-[var(--radius)] px-3 text-left text-sm text-foreground hover:bg-muted focus-visible:bg-muted focus-visible:outline-none md:min-h-[36px]";

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
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Keyboard: focus moves into the menu when it opens, arrows move between
  // items, Esc closes and returns focus to the avatar.
  useEffect(() => {
    if (open) menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);
  const onMenuKeyDown = (e: React.KeyboardEvent) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => items[(n + items.length) % items.length]?.focus();
    if (e.key === "ArrowDown") (e.preventDefault(), go(i + 1));
    else if (e.key === "ArrowUp") (e.preventDefault(), go(i - 1));
    else if (e.key === "Home") (e.preventDefault(), go(0));
    else if (e.key === "End") (e.preventDefault(), go(items.length - 1));
  };

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  if (loading) {
    return <div className="h-11 w-11 shrink-0 animate-pulse rounded-full bg-muted md:h-9 md:w-9" />;
  }

  if (!user) {
    return (
      <button
        type="button"
        onClick={() => void signIn()}
        className={headerControl}
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
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="flex h-11 w-11 items-center justify-center overflow-hidden rounded-full border border-border bg-muted text-sm font-medium text-muted-foreground hover:ring-2 hover:ring-primary/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring md:h-9 md:w-9"
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
          ref={menuRef}
          role="menu"
          aria-label="Account"
          onKeyDown={onMenuKeyDown}
          className="absolute right-0 top-full z-50 mt-2 w-64 rounded-[var(--radius)] border border-border bg-popover p-1 shadow-lg motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-top-1 motion-safe:duration-150"
        >
          <div className="border-b px-3 py-2">
            <p className="truncate text-sm font-medium text-foreground">
              {user.displayName || "Signed in"}
            </p>
            {user.email && (
              <p className="truncate text-xs text-muted-foreground">{user.email}</p>
            )}
          </div>
          {LINKS.filter((l) => !l.admin || isAdmin).map((l) => (
            <Link key={l.href} href={l.href} role="menuitem" onClick={() => setOpen(false)} className={`mt-1 ${ROW}`}>
              <span className="text-muted-foreground" aria-hidden>
                {l.icon}
              </span>
              {l.label}
            </Link>
          ))}
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className={`mt-1 border-t border-border ${ROW}`}
          >
            <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
