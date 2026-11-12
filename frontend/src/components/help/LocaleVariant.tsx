"use client";

import type { ReactNode } from "react";

import { useLocale } from "@/i18n";
import { pickVariant } from "@/i18n/resolve";

/**
 * Shows the translation for the person's language, else `children` (the source).
 *
 * The language is a browser preference, so the server can't choose: /help
 * renders the source and each translation this deployment can show, and this
 * picks one along the fallback chain. The server render and the first client
 * render use the deployment default, so they agree; a stored choice swaps in
 * after hydration. Only the chosen variant reaches the DOM (no duplicate ids).
 * /help only wraps a page in this when it HAS a translation to show, so an
 * English-only deployment renders exactly as before.
 */
export function LocaleVariant({ variants, children }: { variants: Readonly<Record<string, ReactNode>>; children: ReactNode }) {
  const locale = useLocale();
  const chosen = pickVariant(variants, locale);
  return <>{chosen === undefined ? children : chosen}</>;
}
