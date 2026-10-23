"use client";

import type { ReactNode } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";

/**
 * The user guide is for signed-in users, like the rest of the app. The pages are
 * static and their content is synthetic by construction (the same pages ship in
 * the public template), so this gate is about the experience, not secrecy.
 */
export function HelpGate({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <p className="px-6 py-12 text-sm text-muted-foreground">Loading…</p>;
  }
  if (!user) return <SignInRequired skillName="the user guide" />;
  return <>{children}</>;
}
