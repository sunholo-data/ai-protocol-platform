"use client";

import Link from "next/link";
import { use, useEffect } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { useProject } from "@/hooks/useProjects";
import { useProjectChatHref } from "@/hooks/useProjectChatHref";
import { useOptionalRouter } from "@/lib/useOptionalRouter";

/**
 * `/projects/{projectId}/chat` — one stable link to "this project's chats":
 * resolves the project's assistant and replaces itself with the scoped chat.
 * Lets a notice anywhere ("Added to X · Open") open the project the same way the
 * sidebar does, without knowing the user's skills (projects design §6.2).
 */
export default function ProjectChatRedirect({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const { user, loading } = useAuth();
  if (!loading && !user) return <SignInRequired />;
  if (loading || !user) return <p className="p-6 text-sm text-muted-foreground">Opening project…</p>;
  return <Redirect projectId={decodeURIComponent(projectId)} uid={user.uid} />;
}

function Redirect({ projectId, uid }: { projectId: string; uid: string }) {
  const router = useOptionalRouter();
  const { project, status } = useProject(projectId);
  const hrefFor = useProjectChatHref(uid);
  const href = project ? hrefFor(project) : null;

  useEffect(() => {
    if (href) router?.replace(href);
  }, [href, router]);

  if (status === "not_found") {
    return (
      <div role="alert" className="p-6 text-sm">
        <p className="text-foreground">This project was deleted.</p>
        <Link href="/projects" className="text-primary underline underline-offset-4">
          All projects
        </Link>
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert" className="p-6 text-sm">
        <p className="text-foreground">Couldn&apos;t open this project.</p>
        <Link href={`/projects/${encodeURIComponent(projectId)}`} className="text-primary underline underline-offset-4">
          Project settings
        </Link>
      </div>
    );
  }
  return (
    <p role="status" className="p-6 text-sm text-muted-foreground">
      Opening project…
    </p>
  );
}
