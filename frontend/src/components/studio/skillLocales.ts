// Skill Studio — editing a skill's `locales` block (multi-language.md §5).
//
// Every translation is optional. The draft only ever holds FILLED fields: an
// emptied field is removed, an emptied locale is removed, and a skill with no
// translations has no `locales` at all — so a skill is never half-broken by a
// blank translation, and a skill nobody translated saves exactly as before.

import type { SkillLocaleField, SkillLocales } from "@/types/skill";

/** Set one field of one locale; "" removes it. Undefined when nothing is left. */
export function setLocaleField(
  locales: SkillLocales | undefined,
  tag: string,
  field: SkillLocaleField,
  value: string,
): SkillLocales | undefined {
  const entry = { ...(locales?.[tag] ?? {}) };
  if (value === "") delete entry[field];
  else entry[field] = value;
  const next: SkillLocales = { ...(locales ?? {}) };
  if (Object.keys(entry).length) next[tag] = entry;
  else delete next[tag];
  return Object.keys(next).length ? next : undefined;
}

/** Drop whitespace-only fields and the locales they leave empty. */
export function pruneLocales(locales: SkillLocales | undefined | null): SkillLocales {
  const out: SkillLocales = {};
  for (const [tag, entry] of Object.entries(locales ?? {})) {
    const kept = Object.fromEntries(Object.entries(entry ?? {}).filter(([, v]) => typeof v === "string" && v.trim() !== ""));
    if (Object.keys(kept).length) out[tag] = kept;
  }
  return out;
}

/**
 * What Save sends for `locales`. Undefined means "leave the key out" — the
 * skill had no translations and still has none, so nothing about it changes.
 * Otherwise the whole map (the API replaces it): `{}` when the last
 * translation was cleared.
 */
export function localesForSave(locales: SkillLocales | undefined, hadLocales: boolean): SkillLocales | undefined {
  const pruned = pruneLocales(locales);
  if (!Object.keys(pruned).length && !hadLocales) return undefined;
  return pruned;
}
