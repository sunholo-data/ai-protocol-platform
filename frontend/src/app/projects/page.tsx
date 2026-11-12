"use client";

import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { ProjectsList } from "@/components/projects/ProjectsList";
import { PageLoading } from "@/components/ui/skeleton";

/** `/projects` — the user's projects (projects design §6.2). */
export default function ProjectsPage() {
  const { user, loading } = useAuth();
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <PageLoading />;
  return (
    <main className="bg-background">
      <ProjectsList uid={user.uid} />
    </main>
  );
}
