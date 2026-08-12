import Link from "next/link";
import { BRANDING } from "@/lib/branding";
import { ProtocolIcon, type ProtocolIconKey } from "./ProtocolIcon";

export function ProtocolStripe() {
  const { demo } = BRANDING;
  const hasTechRoute = Boolean(demo.techHref);
  return (
    <section className="border-y border-border bg-muted/20">
      <div className="mx-auto w-full max-w-7xl px-6 py-10 md:px-10">
        <div className="mb-6 flex items-end justify-between">
          <h2 className="font-mono text-[10px] uppercase tracking-[0.25em] text-muted-foreground">
            Built on
          </h2>
          {hasTechRoute ? (
            <Link
              href={demo.techHref}
              className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground transition-colors hover:text-primary"
            >
              See the full stack ↗
            </Link>
          ) : null}
        </div>
        <ul className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 lg:grid-cols-6">
          {demo.pillars.map((pillar) => {
            const tileHref = hasTechRoute
              ? `${demo.techHref}#${pillar.key}`
              : pillar.spec ?? "#";
            const external = !hasTechRoute && Boolean(pillar.spec);
            return (
              <li key={pillar.key} className="relative bg-background">
                <Link
                  href={tileHref}
                  target={external ? "_blank" : undefined}
                  rel={external ? "noopener noreferrer" : undefined}
                  className="group flex h-full flex-col gap-2 p-4 transition-colors hover:bg-muted/40"
                >
                  <ProtocolIcon
                    pillar={pillar.key as ProtocolIconKey}
                    className="h-5 w-5 text-muted-foreground transition-colors group-hover:text-primary"
                  />
                  <span className="text-sm font-semibold tracking-tight text-foreground">
                    {pillar.label}
                  </span>
                  <span className="text-xs leading-snug text-muted-foreground">
                    {pillar.tagline}
                  </span>
                </Link>
                {pillar.spec && hasTechRoute ? (
                  <a
                    href={pillar.spec}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="absolute right-2 top-2 rounded px-1 font-mono text-[9px] uppercase tracking-wider text-muted-foreground/70 transition-colors hover:text-primary"
                    aria-label={`${pillar.label} spec — opens in a new tab`}
                  >
                    spec ↗
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
