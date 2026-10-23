"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { YourInstructionsCard } from "@/components/projects/YourInstructionsCard";

/** Settings → Your instructions (projects design §6.5). */
export default function InstructionsSettingsPage() {
  const { user, loading } = useAuth();
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  return (
    <main className="mx-auto min-h-screen max-w-2xl space-y-6 bg-background px-4 py-6 md:px-6">
      <Link href="/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden /> Back to chat
      </Link>
      <h1 className="text-2xl font-semibold text-foreground">Your instructions</h1>
      <YourInstructionsCard />
    </main>
  );
}
