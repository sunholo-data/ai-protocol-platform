"use client";

import Link from "next/link";
import { CircleHelp, LogOut, NotebookPen, Plus, Shield } from "lucide-react";
import { SettingsIcon } from "@/components/icons";
import { useAuth } from "@/contexts/AuthContext";
import { useAdminScope } from "@/hooks/useAdminScope";
import { useAccountActions } from "@/components/shells/ShellActionsContext";
import { NotificationsToggle } from "./NotificationsToggle";

/**
 * The account block at the foot of the phone menu sheet. On a phone the header
 * shows only menu · skill · new chat, so who you are and the skill-level
 * actions (Configure, Create, Admin, Sign out) live here instead of behind an
 * avatar button. Desktop keeps the header's UserMenu.
 */
export function AccountPanel() {
  const { user, signOut } = useAuth();
  const account = useAccountActions();
  const { isAdmin } = useAdminScope(true);
  if (!user) return null;

  const name = user.displayName || user.email || "Signed in";
  const initial = name.charAt(0).toUpperCase();
  const row =
    "flex min-h-[44px] w-full items-center gap-3 rounded-[var(--radius)] px-3 text-left text-sm text-foreground hover:bg-muted";

  return (
    <div className="border-t border-border p-2" data-testid="account-panel">
      <div className="flex items-center gap-3 px-3 py-2">
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
      </div>
      {account?.onConfigure ? (
        <button type="button" className={row} onClick={account.onConfigure}>
          <SettingsIcon className="h-4 w-4 text-muted-foreground" /> Configure this skill
        </button>
      ) : null}
      {account ? (
        <button type="button" className={row} onClick={account.onCreate}>
          <Plus className="h-4 w-4 text-muted-foreground" aria-hidden /> Create a new skill
        </button>
      ) : null}
      <NotificationsToggle rowClassName={row} />
      <Link href="/settings/instructions" className={row}>
        <NotebookPen className="h-4 w-4 text-muted-foreground" aria-hidden /> Your instructions
      </Link>
      <Link href="/help" className={row}>
        <CircleHelp className="h-4 w-4 text-muted-foreground" aria-hidden /> Help
      </Link>
      {isAdmin ? (
        <Link href="/admin" className={row}>
          <Shield className="h-4 w-4 text-muted-foreground" aria-hidden /> Admin
        </Link>
      ) : null}
      <button type="button" className={row} onClick={() => void signOut()}>
        <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden /> Sign out
      </button>
    </div>
  );
}
