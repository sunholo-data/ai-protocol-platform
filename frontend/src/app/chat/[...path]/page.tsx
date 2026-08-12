"use client";

import { useSearchParams } from "next/navigation";
import { use } from "react";
import { useAuth } from "@/contexts/AuthContext";
import type { User } from "@/lib/firebase";
import type { SkillShell } from "@/types/skill";
import { useStableThreadId } from "@/hooks/useStableThreadId";
import { useSlugResolution } from "@/hooks/useSlugResolution";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { SkillNotFound } from "@/components/chat/SkillNotFound";
import { AGUIProvider } from "@/providers/AGUIProvider";
import { ShellRouter } from "@/components/shells/ShellRouter";

/**
 * Chat route entry.
 *
 * v6.4.0 SHELL-MODES: the page is now a thin auth gate + ShellRouter. The
 * skill's page-level shell shape (`shell.mode`) is resolved by
 * useSlugResolution off the same by-slug fetch that yields the skill UUID, so
 * ShellRouter picks the shell with no extra round-trip and no mount-then-swap
 * flash. The actual chat UI lives in components/chat/ChatShell.tsx; the
 * doc-compare / workbench-primary shells live in components/shells/.
 */
export default function ChatPage({
  params,
}: {
  params: Promise<{ path: string[] }>;
}) {
  const { path } = use(params);
  const { user, loading } = useAuth();
  // Wait for auth to hydrate AND the user to be signed in before firing the
  // by-slug fetch. Without `user` in the gate, an unauth visitor would fire
  // a tokenless request, get 401, and see "Skill not found" before the
  // sign-in gate kicks in.
  const { skillId, shell, loading: resolving, notFound } = useSlugResolution(path, !loading && !!user);

  // v6.4.0 INTERNAL-SHELL M3: auth gate is now a stay-on-URL <SignInRequired/>
  // panel (replaces silent `router.replace("/")`) so post-sign-in Firebase
  // auth re-renders directly into the chat the user wanted. Bookmark-safe.
  if (!loading && !user) return <SignInRequired />;

  if (loading || resolving) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (!user) return null;
  if (notFound || !skillId) {
    return <SkillNotFound slug={path[1] ?? path[0]} />;
  }

  // path is validated by useSlugResolution. Two shapes: /chat/@owner/slug
  // (length 2) and the slug-less fallback /chat/{skillId} (length 1). Build the
  // prefix to match — a bare-id path must NOT become "/chat/{id}/undefined"
  // (which would corrupt the ?session= URL writeback).
  const pathPrefix = path.length >= 2 ? `/chat/${path[0]}/${path[1]}` : `/chat/${path[0]}`;

  return <ChatPageInner skillId={skillId} pathPrefix={pathPrefix} user={user} shell={shell} />;
}

function ChatPageInner({
  skillId,
  pathPrefix,
  user,
  shell,
}: {
  skillId: string;
  pathPrefix: string;
  user: User;
  shell: SkillShell | null;
}) {
  const searchParams = useSearchParams();
  const urlSessionId = searchParams.get("session");
  // chat-history-deep-fixes-2 Bug A': pre-allocate a stable threadId so the
  // URL-writeback effect after the first turn doesn't change AGUIProvider's
  // sessionId prop. Without this, useMemo([sessionId, …]) rebuilds the
  // HttpAgent the moment ?session= is written, agent.messages is destroyed,
  // and the user sees turn 1 vanish until the GET refills initialMessages.
  const stableThreadId = useStableThreadId(urlSessionId);

  return (
    <AGUIProvider skillId={skillId} sessionId={stableThreadId}>
      <ShellRouter skillId={skillId} pathPrefix={pathPrefix} user={user} shell={shell} />
    </AGUIProvider>
  );
}
