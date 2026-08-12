"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { BRANDING } from "@/lib/branding";

interface HeroProps {
  visual?: ReactNode;
}

export function Hero({ visual }: HeroProps) {
  const { demo } = BRANDING;
  return (
    <section className="relative mx-auto w-full max-w-7xl px-6 pb-24 pt-16 md:px-10 md:pb-32 md:pt-24">
      <div
        className={
          visual
            ? "grid items-start gap-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16"
            : "flex flex-col items-start gap-8"
        }
      >
        <div className="flex flex-col gap-8">
          <Eyebrow text={demo.heroEyebrow} />
          <h1 className="text-5xl font-bold leading-[0.95] tracking-tight text-foreground sm:text-6xl lg:text-7xl">
            {demo.heroLineA}
            <br />
            <span className="text-primary">{demo.heroLineB}</span>
          </h1>
          <p className="max-w-xl text-base leading-relaxed text-muted-foreground md:text-lg">
            {demo.heroBody}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Link
              href={demo.chatHref}
              className="group inline-flex items-center gap-2 rounded-md bg-primary px-5 py-3 text-sm font-semibold text-primary-foreground shadow-[0_0_18px_hsl(var(--primary)/0.25)] transition-all hover:shadow-[0_0_28px_hsl(var(--primary)/0.45)]"
            >
              {demo.ctaPrimary}
              <ArrowIcon className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <Link
              href={demo.chatHrefSecondary}
              className="inline-flex items-center gap-2 rounded-md border border-border px-5 py-3 text-sm font-semibold text-foreground transition-colors hover:border-primary/50 hover:text-primary"
            >
              {demo.ctaSecondary}
              <span aria-hidden className="text-muted-foreground">
                ↗
              </span>
            </Link>
          </div>
        </div>
        {visual ? <div>{visual}</div> : null}
      </div>
    </section>
  );
}

function Eyebrow({ text }: { text: string }) {
  return (
    <div className="inline-flex items-center gap-2 self-start rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-primary">
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
      </span>
      {text}
    </div>
  );
}

function ArrowIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path
        d="M3 8h10M9 4l4 4-4 4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
