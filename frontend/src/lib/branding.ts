/**
 * Branding — the single file a public fork rebrands.
 *
 * Every user-visible product string in chrome (page titles, marketing
 * copy, contact email, the welcome screen logo path) lives here. A
 * downstream fork rewrites this one file; everything else is generic
 * protocol-stack code.
 *
 * Upstream identity: Sunholo (the public template at
 * sunholo-data/ai-protocol-platform). Downstream consumers (Aitana,
 * AIPLA, etc.) override this object in their own forks.
 *
 * NOT in scope here:
 * - Skill content (lives in Firestore + skill templates)
 * - User-uploaded assets (lives in GCS)
 * - SVG logo file itself (lives in /public/images/logo/, swap the file)
 */

/**
 * Citation URI scheme used by the agent backend to embed document-block links.
 * Format: `{CITATION_SCHEME}://doc/{docId}/block/{blockId}`
 *
 * Forks: set NEXT_PUBLIC_CITATION_SCHEME in .env.local (or Cloud Build substitution)
 * to rebrand this URI without touching component code.
 */
export const CITATION_SCHEME =
  process.env.NEXT_PUBLIC_CITATION_SCHEME || "aitana";

/**
 * Internal transport field name injected into MCP App postMessage payloads.
 * Forks: set NEXT_PUBLIC_APP_SLUG to change the prefix (e.g. "myapp" → "__myappTransport").
 */
// G20 (template-fork-ergonomics.md): `||` not `??`. Cloud Run + Next pre-declare
// every NEXT_PUBLIC_* var (build-arg pattern); absent vars arrive as "", which
// `??` doesn't catch (only null/undefined). `||` treats "" the same as unset.
export const TRANSPORT_FIELD = `__${process.env.NEXT_PUBLIC_APP_SLUG || "platform"}Transport`;

/**
 * Per-deployment branding (v6.4.0 ONE-DEMO M1).
 *
 * Every field reads NEXT_PUBLIC_BRAND_* from the build environment with the
 * Sunholo strings/paths as fallbacks. The public template at
 * sunholo-data/ai-protocol-platform ships without these env vars set, so
 * Sunholo renders. Each fork sets its own NEXT_PUBLIC_BRAND_* in its
 * FIREBASE_ENV Secret Manager secret to rebrand without touching this file.
 *
 * Why `||` not `??`: Cloud Run + Next.js pre-declare every NEXT_PUBLIC_* var
 * at build time; absent vars arrive as empty strings, which `??` doesn't
 * catch (only null/undefined). `||` treats "" the same as unset, falling
 * back to Sunholo defaults. (Same pattern as TRANSPORT_FIELD above.)
 *
 * See docs/design/v6.4.0/multi-tenant-demo-readiness.md.
 */
export const BRANDING = {
  /** Short product name used in page <title>, banners, marketing hero. */
  appName: process.env.NEXT_PUBLIC_BRAND_APP_NAME || "Sunholo",

  /** One-line product tagline shown under the logo on the welcome screen. */
  tagline: process.env.NEXT_PUBLIC_BRAND_TAGLINE || "AI Protocol Platform",

  /** Long form description used in <meta name="description">. */
  description:
    process.env.NEXT_PUBLIC_BRAND_DESCRIPTION ||
    "Open-source AI protocol platform — Skills + AG-UI + A2UI + MCP Apps + A2A on Google ADK",

  /** Public-facing logo paths. Swap the files in /public/images/logo/ to
   * rebrand without touching this object — or set NEXT_PUBLIC_BRAND_LOGO_*
   * to point at fork-specific files. */
  logo: {
    /** Browser tab favicon. */
    favicon: process.env.NEXT_PUBLIC_BRAND_FAVICON || "/images/logo/sunholo-logo.svg",
    /** Big welcome/landing mark (may be a wide wordmark logo). */
    heroAnimated: process.env.NEXT_PUBLIC_BRAND_LOGO_HERO || "/images/logo/sunholo-logo.svg",
    /** The compact brand MARK used everywhere small: the top-left nav mark AND
     * every chat-bubble avatar. Should fill a circle at ~28px (a tight square
     * mark), unlike a wide hero wordmark which shrinks to an illegible smudge. */
    chatAvatar: process.env.NEXT_PUBLIC_BRAND_LOGO_AVATAR || "/images/logo/sunholo-logo.svg",
  },

  /** Contact / community links exposed in CONTRIBUTING + workshop docs. */
  contact: {
    email: process.env.NEXT_PUBLIC_BRAND_EMAIL || "multivac@sunholo.com",
    githubRepo:
      process.env.NEXT_PUBLIC_BRAND_GITHUB || "https://github.com/sunholo-data/ai-protocol-platform",
  },

  /**
   * Landing-page demo copy + CTA targets (v6.4.0 ONE-DEMO M3.5).
   *
   * Two CTAs are rendered on the Hero — both navigate to chat skills by
   * default. Upstream defaults are Sunholo-neutral so the public template
   * keeps a working demo without ONE-specific content. Each fork overrides
   * via NEXT_PUBLIC_BRAND_DEMO_* env vars in Cloud Build.
   *
   * `pillars` is intentionally a deploy-time constant (not env-driven) —
   * the protocol stack the platform showcases is the same across forks.
   * A fork that wants different pillars patches this file.
   *
   * `techHref` — empty string disables the "See the full stack" link on
   * ProtocolStripe. Set it to "/about" or "/tech" if the fork ships that
   * route (4.1 M2 ships /tech upstream later).
   */
  demo: {
    heroEyebrow:
      process.env.NEXT_PUBLIC_BRAND_DEMO_HERO_EYEBROW || "Contract intelligence",
    heroLineA: process.env.NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_A || "Document review",
    heroLineB:
      process.env.NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_B || "with full traceability",
    heroBody:
      process.env.NEXT_PUBLIC_BRAND_DEMO_HERO_BODY ||
      "Compare and analyse documents side-by-side. Every clause extracted, every value cited back to its source.",
    ctaPrimary: process.env.NEXT_PUBLIC_BRAND_DEMO_CTA_PRIMARY || "Open the assistant",
    ctaSecondary: process.env.NEXT_PUBLIC_BRAND_DEMO_CTA_SECONDARY || "Compare documents",
    // Defaults point at the platform's marketplace landing rather than a
    // specific skill so the public template doesn't 404 when no skills
    // are installed. Forks override via NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF*
    // to point at their own primary/secondary skills.
    chatHref: process.env.NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF || "/",
    chatHrefSecondary:
      process.env.NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF_SECONDARY || "/",
    techHref: process.env.NEXT_PUBLIC_BRAND_DEMO_TECH_HREF || "",
    // Business-capability pillars — what the platform DOES, not what it's
    // built on. Each fork's marketing speaks to the buyer of that vertical
    // (legal, energy, procurement). Forks override the array by patching
    // this file in their own clone. Protocol-level reference docs live
    // separately under /tech (forks ship that route when relevant).
    pillars: [
      {
        key: "extract",
        label: "Clause extraction",
        tagline: "Every obligation, typed",
        spec: undefined,
      },
      {
        key: "compare",
        label: "Side-by-side review",
        tagline: "Diff any two contracts",
        spec: undefined,
      },
      {
        key: "benchmark",
        label: "Market benchmark",
        tagline: "Priced terms vs live market",
        spec: undefined,
      },
      {
        key: "compliance",
        label: "Compliance check",
        tagline: "Cross-referenced to regulation",
        spec: undefined,
      },
      {
        key: "citation",
        label: "Clause-level citation",
        tagline: "Every value linked to source",
        spec: undefined,
      },
      {
        key: "confidential",
        label: "Confidential by design",
        tagline: "Your data, your cloud",
        spec: undefined,
      },
    ],
  },
};

export type Branding = typeof BRANDING;
