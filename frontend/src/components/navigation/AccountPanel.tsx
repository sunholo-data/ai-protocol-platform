"use client";

import Link from "next/link";
import { ChevronUp, CircleHelp, FolderKanban, LogOut, Settings, Shield } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useAdminScope } from "@/hooks/useAdminScope";
import { LanguageSwitch } from "@/components/settings/LanguageSwitch";

/**
 * The account block at the foot of the phone menu sheet: the same five links
 * as the desktop avatar menu (UserMenu). Skill actions are in the skill picker.
 */
export function AccountPanel() {
  const { user, signOut } = useAuth();
  const { isAdmin } = useAdminScope(true);
  if (!user) return null;

  const name = user.displayName || user.email || "Signed in";
  const initial = name.charAt(0).toUpperCase();
  const row =
    "flex min-h-[44px] w-full items-center gap-3 rounded-[var(--radius)] px-3 text-left text-sm text-foreground hover:bg-muted";

  return (
    // One row (who you are) that opens to the links, so the drawer's first
    // view is chats and files, not an account menu.
    <details className="group/account border-t border-border p-2" data-testid="account-panel">
      <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-3 rounded-[var(--radius)] px-3 py-1 hover:bg-muted [&::-webkit-details-marker]:hidden">
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border border-border bg-muted text-sm font-medium text-muted-foreground"
        >
          {user.photoURL ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={user.photoURL} alt="" className="h-full w-full object-cover" />
          ) : (
            initial
          )}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold text-foreground">{name}</span>
          {user.displayName && user.email ? (
            <span className="block truncate text-xs text-muted-foreground">{user.email}</span>
          ) : null}
        </span>
        <ChevronUp className="ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open/account:rotate-180" aria-hidden />
      </summary>
      <Link href="/settings" className={row}>
        <Settings className="h-4 w-4 text-muted-foreground" aria-hidden /> Settings
      </Link>
      <Link href="/projects" className={row}>
        <FolderKanban className="h-4 w-4 text-muted-foreground" aria-hidden /> Projects
      </Link>
      <Link href="/help" className={row}>
        <CircleHelp className="h-4 w-4 text-muted-foreground" aria-hidden /> Help
      </Link>
      {isAdmin ? (
        <Link href="/admin" className={row}>
          <Shield className="h-4 w-4 text-muted-foreground" aria-hidden /> Admin
        </Link>
      ) : null}
      <LanguageSwitch className="mx-3 my-2" />
      <button type="button" className={row} onClick={() => void signOut()}>
        <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden /> Sign out
      </button>
    </details>
  );
}
