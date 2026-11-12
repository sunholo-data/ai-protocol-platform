"use client";

import { use } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { ProjectSettings } from "@/components/projects/ProjectSettings";
import { PageLoading } from "@/components/ui/skeleton";

/** `/projects/{projectId}` — a project's settings (projects design §6.2). */
export default function ProjectPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const { user, loading } = useAuth();
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <PageLoading />;
  return (
    <main className="min-h-screen bg-background">
      <ProjectSettings projectId={decodeURIComponent(projectId)} uid={user.uid} />
    </main>
  );
}
