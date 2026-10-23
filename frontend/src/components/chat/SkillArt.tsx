"use client";

import { useEffect, useState } from "react";

/** Where a deployment keeps per-skill illustrations: `<dir>/<slug>.webp`.
 * Unset → no skill art anywhere (the template default). */
const ART_DIR = (process.env.NEXT_PUBLIC_BRAND_SKILL_ART_DIR || "").replace(/\/+$/, "");

/**
 * A skill's illustration as a soft background behind the fresh-chat landing.
 *
 * Optional per skill: a deployment drops `<slug>.webp` into
 * NEXT_PUBLIC_BRAND_SKILL_ART_DIR and that skill gets art; a skill without a
 * file simply 404s once and renders nothing. Faded at the edges with a mask so
 * the welcome text and composer above it stay readable, and faded out entirely
 * (`visible=false`) once the conversation starts.
 */
export function SkillArt({ slug, visible }: { slug: string | null; visible: boolean }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [slug]);
  if (!ART_DIR || !slug || failed) return null;

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`${ART_DIR}/${encodeURIComponent(slug)}.webp`}
      alt=""
      aria-hidden
      onError={() => setFailed(true)}
      data-testid="skill-art"
      className={
        "pointer-events-none absolute inset-x-0 top-0 z-0 mx-auto h-[70%] w-full max-w-4xl select-none object-contain object-top transition-opacity duration-700 " +
        "[mask-image:radial-gradient(ellipse_at_50%_35%,black_35%,transparent_75%)] " +
        (visible ? "opacity-50 md:opacity-40" : "opacity-0")
      }
    />
  );
}
