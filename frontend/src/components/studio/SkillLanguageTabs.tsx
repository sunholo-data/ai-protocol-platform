"use client";

// Skill Studio — one tab per language (multi-language.md §5, decided 2026-11-10).
//
// Only when the deployment offers more than one language. The first tab is the
// original: the existing editor, unchanged, editing the base fields. Each other
// tab edits that language's display name, description and greeting — all
// optional, the original shown greyed as the placeholder. The instructions stay
// on the original tab: one instruction serves every language.

import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { languageName } from "@/components/settings/LanguageSwitch";
import { SOURCE_LOCALE, useT } from "@/i18n";
import { useUserLocale } from "@/i18n/userLocale";
import type { SkillLocaleField, SkillLocales } from "@/types/skill";
import { setLocaleField } from "./skillLocales";

/** The original text each translated field falls back to. */
export interface SkillBaseText {
  displayName: string;
  description: string;
  /** What a fresh chat shows today: welcome intro, else the initial message. */
  greeting: string;
}

interface Props {
  base: SkillBaseText;
  locales: SkillLocales | undefined;
  onLocalesChange: (next: SkillLocales | undefined) => void;
  /** The original editor. Kept mounted while another tab shows, so its own
   * state (and the lists it loaded) survive switching tabs. */
  children: ReactNode;
}

const ORIGINAL = "";

export function SkillLanguageTabs({ base, locales, onLocalesChange, children }: Props) {
  const t = useT("SkillLanguages");
  const { locales: offered } = useUserLocale();
  const [active, setActive] = useState(ORIGINAL);
  const idBase = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const translated = offered.filter((l) => l !== SOURCE_LOCALE);
  if (offered.length < 2 || translated.length === 0) return <>{children}</>;

  const tabs = [ORIGINAL, ...translated];
  const tabId = (tag: string) => `${idBase}-tab-${tag || "original"}`;
  const panelId = (tag: string) => `${idBase}-panel-${tag || "original"}`;
  // A tab for a language the deployment no longer offers can't stay selected.
  const current = tabs.includes(active) ? active : ORIGINAL;

  // Arrow keys move between tabs and select (WAI-ARIA tabs, automatic activation).
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = tabs.length - 1;
    const next =
      e.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : e.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (next === null) return;
    e.preventDefault();
    setActive(tabs[next]);
    tabRefs.current[next]?.focus();
  };

  return (
    <div>
      <div role="tablist" aria-label={t("tablistLabel")} className="flex flex-wrap gap-1 border-b px-4 pt-3">
        {tabs.map((tag, i) => {
          const selected = tag === current;
          const filled = tag !== ORIGINAL && Boolean(locales?.[tag]);
          return (
            <button
              key={tag || "original"}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              type="button"
              role="tab"
              id={tabId(tag)}
              aria-selected={selected}
              aria-controls={panelId(tag)}
              tabIndex={selected ? 0 : -1}
              lang={tag || undefined}
              onClick={() => setActive(tag)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={
                "-mb-px min-h-[44px] rounded-t-[var(--radius)] border-b-2 px-3 text-sm md:min-h-[36px] " +
                (selected
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground")
              }
            >
              {tag === ORIGINAL ? t("original") : languageName(tag)}
              {/* A dot marks a language that has any translation. */}
              {filled && <span aria-hidden className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-primary align-middle" />}
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={panelId(ORIGINAL)} aria-labelledby={tabId(ORIGINAL)} hidden={current !== ORIGINAL}>
        {children}
      </div>

      {translated.map((tag) => (
        <div key={tag} role="tabpanel" id={panelId(tag)} aria-labelledby={tabId(tag)} hidden={current !== tag}>
          {current === tag && (
            <LocaleFields
              tag={tag}
              base={base}
              values={locales?.[tag] ?? {}}
              onChange={(field, value) => onLocalesChange(setLocaleField(locales, tag, field, value))}
            />
          )}
        </div>
      ))}
    </div>
  );
}

function LocaleFields({
  tag,
  base,
  values,
  onChange,
}: {
  tag: string;
  base: SkillBaseText;
  values: Partial<Record<SkillLocaleField, string>>;
  onChange: (field: SkillLocaleField, value: string) => void;
}) {
  const t = useT("SkillLanguages");
  const id = useId();
  const inputClass = "w-full rounded-md border px-3 py-2 text-sm placeholder:text-muted-foreground/70";
  return (
    <div className="space-y-6 p-4">
      <p className="text-xs text-muted-foreground">{t("intro", { language: languageName(tag) })}</p>

      <div className="space-y-1">
        <label htmlFor={`${id}-name`} className="block text-sm font-medium">
          {t("displayName")}
        </label>
        <input
          id={`${id}-name`}
          type="text"
          lang={tag}
          value={values.display_name ?? ""}
          placeholder={base.displayName}
          onChange={(e) => onChange("display_name", e.target.value)}
          className={inputClass}
        />
      </div>

      <div className="space-y-1">
        <label htmlFor={`${id}-description`} className="block text-sm font-medium">
          {t("description")}
        </label>
        <input
          id={`${id}-description`}
          type="text"
          lang={tag}
          value={values.description ?? ""}
          placeholder={base.description}
          onChange={(e) => onChange("description", e.target.value)}
          className={inputClass}
        />
      </div>

      <div className="space-y-1">
        <label htmlFor={`${id}-greeting`} className="block text-sm font-medium">
          {t("greeting")}
          <span className="ml-2 text-xs font-normal text-muted-foreground">{t("greetingHint")}</span>
        </label>
        <textarea
          id={`${id}-greeting`}
          lang={tag}
          rows={3}
          value={values.initial_message ?? ""}
          placeholder={base.greeting}
          onChange={(e) => onChange("initial_message", e.target.value)}
          className={inputClass}
        />
      </div>

      <p className="text-xs text-muted-foreground">{t("instructionsNote")}</p>
    </div>
  );
}
