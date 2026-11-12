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
import { ChevronRight } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { useAdminScope } from "@/hooks/useAdminScope";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { PageLoading } from "@/components/ui/skeleton";

type Link = { href: string; label: string };

// Grouped by what an operator is trying to do, not by API surface. Titles and
// links only: the page is a map, each destination explains itself. Labels say
// "workspace" where the code says tenant.
type Area = {
  title: string;
  links: Link[];
  // Platform-only areas are hidden for a tenant admin rather than shown and
  // then refused.
  platformOnly?: boolean;
};

const AREAS: Area[] = [
  {
    title: "Workspace",
    links: [
      { href: "/admin/tenants", label: "Workspace settings" },
      { href: "/admin/tenants", label: "What your users see" },
    ],
  },
  {
    title: "People",
    links: [
      { href: "/admin/users", label: "Users" },
      { href: "/admin/groups", label: "Groups" },
      { href: "/admin/tool-permissions", label: "Tool permissions" },
    ],
  },
  {
    title: "Skills and activity",
    links: [
      { href: "/skills/studio/new", label: "Skill Studio" },
      { href: "/admin/analytics", label: "Sessions" },
      { href: "/admin/audit", label: "Audit trail" },
    ],
  },
  {
    title: "Platform",
    links: [{ href: "/admin/settings", label: "Platform preamble" }],
    platformOnly: true,
  },
];

export default function AdminHub() {
  const { user, loading } = useAuth();
  const { state, domains, isAdmin, isPlatform, recheck } = useAdminScope(!loading && !!user);

  if (loading || state === "loading") return <PageLoading />;
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
    <main className="mx-auto max-w-xl px-4 py-8">
      <h1 className="text-xl font-semibold">Administration</h1>
      {!isPlatform && domains.length > 0 && (
        // A tenant admin seeing a short list should know it is scoped, not
        // that the platform is empty.
        <p className="mt-1 text-sm text-muted-foreground">
          Scoped to {domains.join(", ")}: you&apos;re seeing your workspace only.
        </p>
      )}
      <div className="mt-6 space-y-6">
        {areas.map((a) => (
          <section key={a.title} aria-labelledby={`admin-${a.title}`}>
            <h2 id={`admin-${a.title}`} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {a.title}
            </h2>
            <ul className="mt-2 divide-y divide-border rounded-[var(--radius)] border border-border">
              {a.links.map((l) => (
                <li key={l.label}>
                  <NextLink
                    href={l.href}
                    className="flex items-center justify-between px-4 py-3 text-sm text-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
                  >
                    {l.label}
                    <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                  </NextLink>
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
  return <main className="flex flex-1 items-center justify-center p-8">{children}</main>;
}
