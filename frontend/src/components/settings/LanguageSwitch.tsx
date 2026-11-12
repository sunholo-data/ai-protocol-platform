"use client";

import { useT } from "@/i18n";
import { useUserLocale } from "@/i18n/userLocale";

/**
 * Each language in its OWN name ("Dansk", "English"), so the switch can be read
 * whatever language the app is in now. Falls back to the tag for a locale the
 * browser doesn't know.
 */
export function languageName(tag: string): string {
  try {
    const name = new Intl.DisplayNames([tag], { type: "language" }).of(tag);
    if (name && name !== tag) return name.charAt(0).toLocaleUpperCase(tag) + name.slice(1);
  } catch {
    // Not a tag Intl knows.
  }
  return tag;
}

/**
 * The interface-language switch (multi-language.md §2). Renders nothing when
 * the deployment offers one language — so an English-only fork sees no change.
 */
export function LanguageSwitch({ className }: { className?: string }) {
  const t = useT("LanguageSection");
  const { locale, locales, setLocale } = useUserLocale();
  if (locales.length < 2) return null;
  return (
    <div role="radiogroup" aria-label={t("switchLabel")} className={`inline-flex rounded-[var(--radius)] border border-border p-0.5 ${className ?? ""}`}>
      {locales.map((l) => (
        <button
          key={l}
          type="button"
          role="radio"
          aria-checked={l === locale}
          lang={l}
          onClick={() => setLocale(l)}
          className={`min-h-[36px] rounded-[calc(var(--radius)-2px)] px-3 text-sm ${
            l === locale ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {languageName(l)}
        </button>
      ))}
    </div>
  );
}

/** Settings → Language. Hidden with one language, like the switch. */
export function LanguageSection() {
  const t = useT("LanguageSection");
  const { locales } = useUserLocale();
  if (locales.length < 2) return null;
  return (
    <section id="language" aria-labelledby="language-title" className="scroll-mt-6">
      <h2 id="language-title" className="text-lg font-semibold text-foreground">
        {t("title")}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("body")}</p>
      <LanguageSwitch className="mt-3" />
    </section>
  );
}

/** Whether the Settings list should show a Language entry. */
export function useShowLanguageSection(): boolean {
  return useUserLocale().locales.length > 1;
}
