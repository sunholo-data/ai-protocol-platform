/**
 * ONE landing right-column visual.
 *
 * A single vertical trace of what the platform actually does end-to-end on
 * any contract: a real clause excerpt → the obligation terms extracted from
 * it (typed, mono, cited to the page) → a settlement figure *computed* by
 * the verified engine from exactly those terms. The point for a
 * technical-legal / quant audience is that the arithmetic is theirs to check:
 * the inputs are on screen, and the output is marked verified + model-free
 * (deterministic recompute, not an LLM guess). Deliberately generic contract
 * language (not PPA-specific) — the landing page shouldn't read as a
 * single-vertical tool (2026-08 UAT feedback).
 *
 * Not connected to any agent — it's the marketing teaser that previews what
 * clicking the Hero CTA opens.
 *
 * Fork pattern: each deployment supplies its own HeroVisual via Hero's
 * `visual` prop. Upstream / Sunholo defaults to no visual (single-column
 * Hero). To rebrand, replace this component (or write a sibling like
 * AcmeHeroVisual.tsx) and pass it to `<Hero visual={...} />` in page.tsx.
 */

export function OneHeroVisual() {
  return (
    <div className="relative isolate">
      <div className="relative overflow-hidden rounded-xl border border-border bg-muted/30 p-6 backdrop-blur md:p-7">
        {/* faint engineering grid — signals "instrument", not "brochure" */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.05]"
          style={{
            backgroundImage:
              "linear-gradient(hsl(var(--primary)) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--primary)) 1px, transparent 1px)",
            backgroundSize: "28px 28px",
          }}
        />

        <div className="relative mb-4 flex items-center justify-between">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            Obligation trace
          </span>
          <span className="inline-flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
            live
          </span>
        </div>

        {/* Step 1 — the source clause, as it reads in the contract */}
        <TraceStep index="01" label="Clause · as written">
          <div className="rounded-md border border-border bg-background p-3">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                § 7.3 Delay Liquidated Damages
              </span>
              <span className="font-mono text-[9px] text-muted-foreground">
                acme-supply-agreement.pdf · p.14
              </span>
            </div>
            <p className="font-serif text-[13px] leading-snug text-foreground">
              “If the Supplier fails to achieve Delivery by the Guaranteed
              Delivery Date, the Supplier shall pay{" "}
              <mark className="bg-primary/15 px-0.5 text-foreground">
                €1,500 per day
              </mark>{" "}
              of delay, capped at{" "}
              <mark className="bg-primary/15 px-0.5 text-foreground">
                5% of Contract Value
              </mark>
              .”
            </p>
          </div>
        </TraceStep>

        <TraceConnector />

        {/* Step 2 — typed terms the extractor pulled, each cited */}
        <TraceStep index="02" label="Extracted · typed & cited">
          <div className="rounded-md border border-border bg-background p-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              <Term k="trigger" v="Delivery missed" />
              <Term k="rate" v="€1,500 / day" />
              <Term k="cap" v="5% · €900,000" />
              <Term k="basis" v="calendar days" />
            </dl>
          </div>
        </TraceStep>

        <TraceConnector />

        {/* Step 3 — the computed answer, verified + model-free */}
        <TraceStep index="03" label="Computed · what-if">
          <div className="rounded-md border border-primary/40 bg-primary/[0.05] p-3">
            <div className="mb-2 flex items-baseline justify-between gap-2">
              <span className="text-xs text-muted-foreground">
                Delivery slips{" "}
                <span className="font-mono tabular-nums text-foreground">
                  21 days
                </span>
              </span>
              <span className="font-mono text-lg font-semibold tabular-nums text-foreground">
                €31,500
              </span>
            </div>
            <div className="flex items-center justify-between font-mono text-[10px]">
              <span className="inline-flex items-center gap-1.5 text-primary">
                <CheckIcon className="h-3 w-3" />
                verified · within cap
              </span>
              <span className="tabular-nums text-muted-foreground">
                recomputed in 9&nbsp;ms · 0 tokens
              </span>
            </div>
          </div>
        </TraceStep>
      </div>
    </div>
  );
}

function TraceStep({
  index,
  label,
  children,
}: {
  index: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-mono text-[10px] tabular-nums text-primary">
          {index}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
      </div>
      {children}
    </div>
  );
}

function TraceConnector() {
  return (
    <div
      aria-hidden
      className="relative flex h-4 items-center justify-start pl-[7px]"
    >
      <span className="h-full w-px bg-gradient-to-b from-primary/50 to-primary/10" />
    </div>
  );
}

function Term({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {k}
      </dt>
      <dd className="font-mono tabular-nums text-foreground">{v}</dd>
    </div>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      aria-hidden="true"
    >
      <path
        d="M3 8.5l3.5 3.5L13 4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
