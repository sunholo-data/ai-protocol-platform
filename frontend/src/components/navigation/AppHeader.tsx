"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { BRANDING } from "@/lib/branding";
import { UserMenu } from "./UserMenu";

/**
 * The header every page outside the chat shares: the brand (home), a way back
 * to your chats, and the account menu. The chat has its own richer bar
 * (SkillsBar); a chat route nested under a framed section (a project's chat)
 * renders without this one.
 */
export function AppHeader() {
  return (
    <header
      aria-label="Site"
      className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-4 md:px-6"
    >
      <Link
        href="/"
        aria-label={`${BRANDING.appName} home`}
        className="flex shrink-0 items-center gap-2 rounded-[var(--radius)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={BRANDING.logo.chatAvatar} alt="" className="h-7 w-7 rounded-full" />
        <span className="hidden font-semibold text-foreground sm:inline">{BRANDING.appName}</span>
      </Link>
      <Link
        href="/"
        className="inline-flex items-center gap-1 rounded-[var(--radius)] px-2 py-1 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to chat
      </Link>
      <div className="flex-1" />
      <UserMenu />
    </header>
  );
}

/** Layout frame: the shared header above a scrolling page. */
export function PageFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  if (pathname?.split("/").includes("chat")) return <>{children}</>;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <AppHeader />
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </div>
  );
}
