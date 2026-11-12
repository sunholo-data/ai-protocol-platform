"use client";

// A skill's text as the person reads it (multi-language.md §5).
//
// SKILL.md's `display_name`, `description` and `initial_message` may carry a
// `locales:` block; each field resolves with `localizedField` — the person's
// locale, then its language, then the base. Wherever a person READS these, go
// through here. Where they're EDITED (Skill Studio's source fields) use the
// base values, untouched.

import { localizedField, useLocale, type Locale } from "@/i18n";
import type { SkillLocales, WelcomeConfig } from "@/types/skill";

/** The slice of a skill the text needs — a full `Skill`, or a list row. */
export interface SkillTextSource {
  displayName?: string | null;
  description?: string | null;
  initialMessage?: string | null;
  locales?: SkillLocales | null;
}

export interface SkillText {
  displayName: string;
  description: string;
  initialMessage: string;
}

/** Non-hook form, for lists (resolve each row with one `useLocale()`). */
export function skillText(skill: SkillTextSource | null | undefined, locale: Locale): SkillText {
  const locales = skill?.locales;
  return {
    displayName: localizedField(skill?.displayName, locales, "display_name", locale),
    description: localizedField(skill?.description, locales, "description", locale),
    initialMessage: localizedField(skill?.initialMessage, locales, "initial_message", locale),
  };
}

/** A skill's display name, description and greeting in the person's language. */
export function useSkillText(skill: SkillTextSource | null | undefined): SkillText {
  return skillText(skill, useLocale());
}

/**
 * The greeting a fresh chat opens with: `welcome.introMessage`, else the legacy
 * `initialMessage` (v6.4.0 SKILL-ONBOARDING). A translated `initial_message` for
 * the person's locale wins over BOTH: it is the translation of whichever greeting
 * the skill shows, and an untranslated `introMessage` must not hide it. Null
 * when the skill has none.
 */
export function skillGreeting(
  skill: SkillTextSource & { welcome?: Pick<WelcomeConfig, "introMessage"> | null },
  locale: Locale,
): string | null {
  const translated = localizedField(null, skill.locales, "initial_message", locale).trim();
  return (
    translated ||
    skill.welcome?.introMessage?.trim() ||
    skill.initialMessage?.trim() ||
    null
  );
}
