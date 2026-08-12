// Admin hub — the entry point for the admin surfaces.
//
// v6.16.0 Phase 3: grouped into FIVE task-shaped areas instead of seven cards,
// one per API surface. The old layout mirrored the backend's module structure,
// which answers questions an operator never asks ("what is in tool_permissions?")
// while hiding the one they do ("what do my users actually see?").
//
// v6.16.0: gated by GET /api/admin/whoami (the role probe) instead of probing
// GET /api/admin/clients. The old probe was platform-only, so a legitimate
// tenant admin was told "admins only" on their own admin console. whoami also
// distinguishes "not an admin" from "the backend is down", which the old
// non-ok-means-forbidden read could not.
//
// v6.9.0 administration-overview.md (the /admin shell) + v6.16.0
// admin-console-tenant-readiness.md (scoping).

"use client";

import NextLink from "next/link";
import { useAuth } from "@/contexts/AuthContext";
import { useAdminScope } from "@/hooks/useAdminScope";
import { SignInRequired } from "@/components/chat/SignInRequired";

type Link = { href: string; label: string; note?: string };

type Area = {
  href: string;
  title: string;
  // The QUESTION this area answers. The previous IA had one card per API
  // surface, which mirrored the backend's module layout rather than anything an
  // operator actually asks — fine for the engineer who wrote it, wrong for a
  // client admin. v6.16.0 Phase 3 regroups them by task.
  question: string;
  links: Link[];
  // Platform-only areas are hidden entirely for tenant scope rather than
  // shown-then-403'd: offering a door that always slams is the opposite of
  // the never-silent principle.
  platformOnly?: boolean;
};

const AREAS: Area[] = [
  {
    href: "/admin/tenants",
    title: "Your tenant",
    question: "What is my tenant configured to do, and what do my users actually see?",
    links: [
      { href: "/admin/tenants", label: "Tenant config", note: "enabled skills, landing skill, group tags, bucket" },
      { href: "/admin/tenants", label: "What your users actually see", note: "effective access for one user" },
    ],
  },
  {
    href: "/admin/users",
    title: "People & access",
    question: "Who is here, and what may they use?",
    links: [
      { href: "/admin/users", label: "Users", note: "grant and revoke group tags" },
      { href: "/admin/groups", label: "Group tags", note: "the registry, and who holds each tag" },
      { href: "/admin/tool-permissions", label: "Tool permissions", note: "which tools a user or domain may call" },
    ],
  },
  {
    href: "/skills/studio/new",
    title: "Skills",
    question: "What is published, and to whom?",
    links: [{ href: "/skills/studio/new", label: "Skill Studio", note: "model, tools, instructions, access" }],
  },
  {
    href: "/admin/analytics",
    title: "Activity & audit",
    question: "What happened, and who changed what?",
    links: [
      { href: "/admin/analytics", label: "Sessions", note: "browse past sessions and their traces" },
      { href: "/admin/audit", label: "Audit trail", note: "who changed what, and when" },
    ],
  },
  {
    href: "/admin/settings",
    title: "Platform",
    question: "Settings that apply across every tenant.",
    links: [{ href: "/admin/settings", label: "Platform preamble", note: "prepended to every skill's prompt" }],
    platformOnly: true,
  },
];

export default function AdminHub() {
  const { user, loading } = useAuth();
  const { state, domains, isAdmin, isPlatform, recheck } = useAdminScope(!loading && !!user);

  if (loading || state === "loading") return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;

  // A genuine failure is NOT the same as "you're not an admin" — say so, and
  // offer the retry, rather than implying the user lacks access.
  if (state === "error") {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Couldn&apos;t check your access</h1>
          <p className="text-sm text-muted-foreground">
            We couldn&apos;t reach the admin service. This is a connection problem, not a
            permissions one — please reload to try again.
          </p>
        </div>
      </Centered>
    );
  }

  if (!isAdmin) {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Admins only</h1>
          <p className="text-sm text-muted-foreground">
            The admin area requires the <code>aitana-admin</code> group, or{" "}
            <code>tenant-admin:&lt;your-domain&gt;</code> to administer your own tenant.
          </p>
          {/* Group tags live in the signed JWT, so a JUST-granted claim isn't in
              the current token and won't be for ~1h. Without this button the
              newly-appointed admin sees no link, no error, and no explanation —
              they just wait, which is a silent failure. Forcing a token refresh
              picks the claim up immediately. */}
          <p className="mt-3 text-sm text-muted-foreground">
            Been granted access just now? A new group tag only lands when your sign-in token
            refreshes.
          </p>
          <button
            type="button"
            onClick={() => void recheck()}
            className="mt-2 rounded border px-3 py-1.5 text-sm hover:bg-muted"
          >
            Re-check my access
          </button>
        </div>
      </Centered>
    );
  }

  const areas = AREAS.filter((a) => isPlatform || !a.platformOnly);

  return (
    <main className="mx-auto max-w-4xl p-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold">Administration</h1>
        <p className="text-sm text-muted-foreground">
          Manage tenants, skills, access, and analytics.
        </p>
        {!isPlatform && domains.length > 0 && (
          // Say plainly whose data this is. A tenant admin seeing a short list
          // should know it is scoped, not that the platform is empty.
          <p className="mt-1 text-xs text-muted-foreground">
            Scoped to {domains.join(", ")} — you&apos;re seeing your tenant only.
          </p>
        )}
      </header>
      <div className="grid gap-4 sm:grid-cols-2">
        {areas.map((a) => (
          <section key={a.title} className="rounded-lg border p-4">
            <h2 className="font-medium">{a.title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{a.question}</p>
            <ul className="mt-3 space-y-1.5">
              {a.links.map((l) => (
                <li key={l.label}>
                  <NextLink href={l.href} className="text-sm text-foreground underline-offset-2 hover:underline">
                    {l.label}
                  </NextLink>
                  {l.note && <span className="ml-2 text-xs text-muted-foreground">{l.note}</span>}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </main>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
