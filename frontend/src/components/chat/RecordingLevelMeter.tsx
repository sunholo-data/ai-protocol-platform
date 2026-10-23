"use client";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

const BARS = 5;

/**
 * A tiny equalizer-style meter that animates to the live mic input level, so
 * the user can SEE the mic is capturing (Axiom 11 — never an ambiguous
 * always-listening state). Driven by polling `getLevel()` (0..1) on each
 * animation frame; uses `bg-current` so it takes the colour of its context
 * (e.g. red while recording). Purely decorative — aria-hidden.
 */
export function RecordingLevelMeter({
  getLevel,
  className,
}: {
  getLevel: () => number;
  className?: string;
}) {
  const bars = useRef<(HTMLSpanElement | null)[]>([]);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    let mounted = true;
    const tick = () => {
      if (!mounted) return;
      const level = getLevel();
      for (let i = 0; i < BARS; i++) {
        const bar = bars.current[i];
        if (!bar) continue;
        // Centre bars react more than edges for a lively, symmetric meter.
        const weight = 0.45 + 0.55 * Math.sin((i / (BARS - 1)) * Math.PI);
        const h = Math.max(0.15, Math.min(1, level * weight * 1.6));
        bar.style.transform = `scaleY(${h.toFixed(3)})`;
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      mounted = false;
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [getLevel]);

  return (
    <span className={cn("inline-flex items-center gap-[2px]", className)} aria-hidden="true">
      {Array.from({ length: BARS }).map((_, i) => (
        <span
          key={i}
          ref={(el) => {
            bars.current[i] = el;
          }}
          className="h-3.5 w-[2px] origin-center rounded-full bg-current transition-transform duration-75 ease-out"
          style={{ transform: "scaleY(0.15)" }}
        />
      ))}
    </span>
  );
}
