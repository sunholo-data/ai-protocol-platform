"use client";

import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { ProjectsList } from "@/components/projects/ProjectsList";

/** `/projects` — the user's projects (projects design §6.2). */
export default function ProjectsPage() {
  const { user, loading } = useAuth();
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  return (
    <main className="min-h-screen bg-background">
      <ProjectsList uid={user.uid} />
    </main>
  );
}
