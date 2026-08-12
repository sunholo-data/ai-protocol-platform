import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Per-deployment branding tests (v6.4.0 ONE-DEMO M1).
 *
 * branding.ts reads NEXT_PUBLIC_BRAND_* at module-load time with Sunholo
 * fallbacks. Tests use `vi.resetModules()` + `vi.stubEnv()` to verify both
 * the override path (fork sets vars) and the fallback path (template
 * defaults render — required so sunholo-data/ai-protocol-platform keeps
 * Sunholo branding).
 */

const ALL_BRAND_VARS = [
  "NEXT_PUBLIC_BRAND_APP_NAME",
  "NEXT_PUBLIC_BRAND_TAGLINE",
  "NEXT_PUBLIC_BRAND_DESCRIPTION",
  "NEXT_PUBLIC_BRAND_FAVICON",
  "NEXT_PUBLIC_BRAND_LOGO_HERO",
  "NEXT_PUBLIC_BRAND_LOGO_AVATAR",
  "NEXT_PUBLIC_BRAND_EMAIL",
  "NEXT_PUBLIC_BRAND_GITHUB",
  "NEXT_PUBLIC_BRAND_DEMO_HERO_EYEBROW",
  "NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_A",
  "NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_B",
  "NEXT_PUBLIC_BRAND_DEMO_HERO_BODY",
  "NEXT_PUBLIC_BRAND_DEMO_CTA_PRIMARY",
  "NEXT_PUBLIC_BRAND_DEMO_CTA_SECONDARY",
  "NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF",
  "NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF_SECONDARY",
  "NEXT_PUBLIC_BRAND_DEMO_TECH_HREF",
] as const;

function clearAllBrandVars() {
  for (const v of ALL_BRAND_VARS) {
    vi.stubEnv(v, "");
  }
}

async function freshBranding() {
  vi.resetModules();
  const mod = await import("@/lib/branding");
  return mod.BRANDING;
}

describe("BRANDING — env-var driven per-deployment branding", () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("falls back to Sunholo defaults when no NEXT_PUBLIC_BRAND_* is set", async () => {
    clearAllBrandVars();
    const BRANDING = await freshBranding();
    expect(BRANDING.appName).toBe("Sunholo");
    expect(BRANDING.tagline).toBe("AI Protocol Platform");
    expect(BRANDING.logo.favicon).toBe("/images/logo/sunholo-logo.svg");
    expect(BRANDING.logo.heroAnimated).toBe("/images/logo/sunholo-logo.svg");
    expect(BRANDING.logo.chatAvatar).toBe("/images/logo/sunholo-logo.svg");
    expect(BRANDING.contact.email).toBe("multivac@sunholo.com");
    expect(BRANDING.contact.githubRepo).toBe(
      "https://github.com/sunholo-data/ai-protocol-platform",
    );
  });

  it("uses NEXT_PUBLIC_BRAND_* overrides when a fork sets them (ONE example)", async () => {
    vi.stubEnv("NEXT_PUBLIC_BRAND_APP_NAME", "Acme Energy");
    vi.stubEnv("NEXT_PUBLIC_BRAND_TAGLINE", "PPA & PtX intelligence");
    vi.stubEnv(
      "NEXT_PUBLIC_BRAND_DESCRIPTION",
      "Power Purchase Agreement and Power-to-X transaction advisory",
    );
    vi.stubEnv("NEXT_PUBLIC_BRAND_FAVICON", "/images/logo/acmeenergy-logo.jpg");
    vi.stubEnv("NEXT_PUBLIC_BRAND_LOGO_HERO", "/images/logo/acmeenergy-logo.jpg");
    vi.stubEnv("NEXT_PUBLIC_BRAND_LOGO_AVATAR", "/images/logo/acmeenergy-logo.jpg");
    vi.stubEnv("NEXT_PUBLIC_BRAND_EMAIL", "hello@acme-energy.example");
    vi.stubEnv("NEXT_PUBLIC_BRAND_GITHUB", "");

    const BRANDING = await freshBranding();
    expect(BRANDING.appName).toBe("Acme Energy");
    expect(BRANDING.tagline).toBe("PPA & PtX intelligence");
    expect(BRANDING.description).toBe(
      "Power Purchase Agreement and Power-to-X transaction advisory",
    );
    expect(BRANDING.logo.favicon).toBe("/images/logo/acmeenergy-logo.jpg");
    expect(BRANDING.logo.heroAnimated).toBe("/images/logo/acmeenergy-logo.jpg");
    expect(BRANDING.logo.chatAvatar).toBe("/images/logo/acmeenergy-logo.jpg");
    expect(BRANDING.contact.email).toBe("hello@acme-energy.example");
    // Empty string falls back to upstream — forks that don't have a public
    // repo can leave NEXT_PUBLIC_BRAND_GITHUB unset.
    expect(BRANDING.contact.githubRepo).toBe(
      "https://github.com/sunholo-data/ai-protocol-platform",
    );
  });

  it("mixes overrides and fallbacks per-field (partial fork rebrand)", async () => {
    clearAllBrandVars();
    vi.stubEnv("NEXT_PUBLIC_BRAND_APP_NAME", "Acme AI");
    // Tagline + logos left empty → fall back to Sunholo defaults

    const BRANDING = await freshBranding();
    expect(BRANDING.appName).toBe("Acme AI");
    expect(BRANDING.tagline).toBe("AI Protocol Platform");
    expect(BRANDING.logo.favicon).toBe("/images/logo/sunholo-logo.svg");
  });

  it("treats empty-string env vars as unset (|| fallback, per G20 pattern)", async () => {
    // Cloud Run injects NEXT_PUBLIC_* vars as empty strings when not in
    // FIREBASE_ENV — `??` wouldn't catch these, `||` does. This test
    // protects the G20 pattern: changing `||` to `??` here would silently
    // render empty product strings in prod.
    vi.stubEnv("NEXT_PUBLIC_BRAND_APP_NAME", "");
    vi.stubEnv("NEXT_PUBLIC_BRAND_TAGLINE", "");

    const BRANDING = await freshBranding();
    expect(BRANDING.appName).toBe("Sunholo");
    expect(BRANDING.tagline).toBe("AI Protocol Platform");
  });

  // ── BRANDING.demo (v6.4.0 ONE-DEMO M3.5) ───────────────────────────────

  it("BRANDING.demo falls back to vertical-neutral document-review defaults when env vars unset", async () => {
    clearAllBrandVars();
    const BRANDING = await freshBranding();
    expect(BRANDING.demo.heroEyebrow).toBe("Contract intelligence");
    expect(BRANDING.demo.heroLineA).toBe("Document review");
    expect(BRANDING.demo.heroLineB).toBe("with full traceability");
    expect(BRANDING.demo.heroBody).toContain("side-by-side");
    expect(BRANDING.demo.ctaPrimary).toBe("Open the assistant");
    expect(BRANDING.demo.ctaSecondary).toBe("Compare documents");
    // Default hrefs land on the marketplace ("/") so a fresh template fork
    // never 404s when no skills are installed. Forks override via
    // NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF* to point at their primary skills.
    expect(BRANDING.demo.chatHref).toBe("/");
    expect(BRANDING.demo.chatHrefSecondary).toBe("/");
    expect(BRANDING.demo.techHref).toBe(""); // empty → ProtocolStripe hides "see full stack"
    // Business-capability pillars (what the platform DOES), not protocol stack.
    expect(BRANDING.demo.pillars).toHaveLength(6);
    expect(BRANDING.demo.pillars.map((p) => p.key)).toEqual([
      "extract",
      "compare",
      "benchmark",
      "compliance",
      "citation",
      "confidential",
    ]);
  });

  it("BRANDING.demo uses NEXT_PUBLIC_BRAND_DEMO_* overrides (ONE example)", async () => {
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_HERO_EYEBROW", "Energy intelligence");
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_A", "Side-by-side");
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_B", "PPA contract comparison");
    vi.stubEnv(
      "NEXT_PUBLIC_BRAND_DEMO_HERO_BODY",
      "Compare any two PPA contracts. AILANG-parsed blocks, structured clause extraction, ENTSO-E-grounded price valuation. Built for ONE consultants.",
    );
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_CTA_PRIMARY", "Ask the PPA expert");
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_CTA_SECONDARY", "Compare contracts");
    vi.stubEnv(
      "NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF",
      "/chat/@aitana-platform/one-ppa-expert",
    );
    vi.stubEnv(
      "NEXT_PUBLIC_BRAND_DEMO_CHAT_HREF_SECONDARY",
      "/chat/@aitana-platform/one-doc-compare",
    );

    const BRANDING = await freshBranding();
    expect(BRANDING.demo.heroEyebrow).toBe("Energy intelligence");
    expect(BRANDING.demo.heroLineA).toBe("Side-by-side");
    expect(BRANDING.demo.heroLineB).toBe("PPA contract comparison");
    expect(BRANDING.demo.heroBody).toContain("PPA contracts");
    expect(BRANDING.demo.ctaPrimary).toBe("Ask the PPA expert");
    expect(BRANDING.demo.ctaSecondary).toBe("Compare contracts");
    expect(BRANDING.demo.chatHref).toBe(
      "/chat/@aitana-platform/one-ppa-expert",
    );
    expect(BRANDING.demo.chatHrefSecondary).toBe(
      "/chat/@aitana-platform/one-doc-compare",
    );
  });

  it("BRANDING.demo empty-string env vars fall back per G20 pattern", async () => {
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_HERO_LINE_A", "");
    vi.stubEnv("NEXT_PUBLIC_BRAND_DEMO_CTA_PRIMARY", "");

    const BRANDING = await freshBranding();
    expect(BRANDING.demo.heroLineA).toBe("Document review");
    expect(BRANDING.demo.ctaPrimary).toBe("Open the assistant");
  });
});
