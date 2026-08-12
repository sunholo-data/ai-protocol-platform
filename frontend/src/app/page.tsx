import { HomeGate } from "@/components/home/HomeGate";
import { BackendHealthBadge } from "@/components/BackendHealthBadge";
import { MySkillsButton } from "@/components/MySkillsButton";
import { SignInButton } from "@/components/SignInButton";
import { AudienceBand } from "@/components/landing/AudienceBand";
import { Hero } from "@/components/landing/Hero";
import { OneHeroVisual } from "@/components/landing/OneHeroVisual";
import { ProtocolStripe } from "@/components/landing/ProtocolStripe";
import { skillHref } from "@/components/navigation/skillHref";
import { SkillStatusBadge } from "@/components/skills/SkillStatusBadge";
import { BRANDING } from "@/lib/branding";
import Link from "next/link";

const SHOW_DEV_PROBES = process.env.NEXT_PUBLIC_SHOW_DEV_PROBES === "true";

interface SkillSummary {
  skillId: string;
  ownerId: string;
  slug: string | null;
  /** Internal skill id slug (kebab-case). Use `displayName` for UI. */
  name: string;
  /** Human-readable Proper Case name from SKILL.md `display_name` frontmatter.
   * Falls back to `name` when missing (older skills without display_name). */
  displayName?: string;
  description: string;
  /** Top-level `tags:` from SKILL.md frontmatter. Surfaced by
   * SkillStatusBadge — recognised tags become coloured badges
   * (experimental / dev-tool / a2ui-demo); the rest are metadata. */
  tags?: string[];
}

async function getMarketplaceSkills(): Promise<SkillSummary[]> {
  try {
    // G20 (template-fork-ergonomics.md): use `||` not `??` for env defaults.
    // Cloud Run sets every declared env var; absent vars come through as "",
    // which `??` doesn't catch (it only falls back on null/undefined).
    const backendUrl = process.env.BACKEND_URL || "http://localhost:1956";
    const res = await fetch(`${backendUrl}/api/skills/marketplace?limit=10`, {
      next: { revalidate: 30 },
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export default async function HomePage() {
  const skills = await getMarketplaceSkills();

  return (
    <HomeGate>
    <main className="min-h-screen">
      <header className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 pt-4 md:px-10">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={BRANDING.logo.heroAnimated}
          alt={BRANDING.appName}
          className="h-8 w-8"
        />
        <div className="flex items-center gap-3">
          <BackendHealthBadge />
          {SHOW_DEV_PROBES && <MySkillsButton />}
          <SignInButton />
        </div>
      </header>

      <Hero visual={<OneHeroVisual />} />

      <ProtocolStripe />

      <AudienceBand />

      {skills.length > 0 && (
        <section className="mx-auto w-full max-w-7xl px-6 py-16 md:px-10">
          <div className="mb-6 flex items-end justify-between">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.25em] text-muted-foreground">
              Skills
            </h2>
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              {skills.length} available
            </span>
          </div>
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {skills.map((skill) => (
              <li key={skill.skillId}>
                <Link
                  href={skillHref(skill)}
                  className="group flex h-full flex-col gap-2 rounded-lg border border-border bg-background p-4 transition-colors hover:border-primary/50 hover:bg-muted/40"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <span className="text-sm font-semibold text-foreground group-hover:text-primary">
                      {skill.displayName || skill.name}
                    </span>
                    <SkillStatusBadge tags={skill.tags} />
                  </div>
                  {skill.description && (
                    <span className="line-clamp-2 text-xs text-muted-foreground">
                      {skill.description}
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
    </HomeGate>
  );
}
