# Offering more than one language

**For:** forks whose people read the interface in a language other than English,
or in several.
**Design:** `docs/design/v6.43.0/multi-language.md` (internal).

The template has the infrastructure for more than one language: a message layer,
a language switch, and a place for your translations that never touches a
template file. Nothing changes until you turn it on. With one language
configured there is no switch and every string is the same as before.

## Turn it on

1. **Add a catalog for each language you offer**, in your own folder:

   ```
   docs/customers/<you>/messages/<locale>/<area>.json
   ```

   Copy the template's English catalog (`frontend/messages/en/*.json`) as a
   starting point and translate the values. Keep the keys and the `{placeholders}`
   exactly as they are. A key you don't translate shows in English.

2. **Set the languages** in your frontend environment (your `FIREBASE_ENV`
   secret for deployed builds, `frontend/.env.local` locally):

   ```
   NEXT_PUBLIC_LOCALES=da,en
   NEXT_PUBLIC_DEFAULT_LOCALE=da
   ```

   Locales are BCP 47 tags (`da`, `de`, `pt-BR`). The default is what someone
   who has never chosen sees. People switch in **Settings** (and in the phone
   menu), and their choice is remembered in that browser.

3. **Build.** `npm run dev`/`build`/`test` and Cloud Build bundle the catalogs
   for you (`scripts/i18n/bundle.mjs`). A locale listed in `NEXT_PUBLIC_LOCALES`
   with no catalog stops the build with a message saying so.

## What you can put in your folder

| You want | You add |
|---|---|
| A language | `messages/<locale>/*.json` with template namespaces translated |
| Your own wording for a template string | The same namespace and key under `messages/en/` (or any locale). The bundle lists every override |
| Your own screens translatable | New namespaces (by convention the component name), then `useT("YourNamespace")` in your components |

A key that a template namespace doesn't have is refused, so a typo can't
silently do nothing. Two of your files defining the same new namespace are
refused too.

## Using it in your own components

```tsx
import { useT } from "@/i18n";

const t = useT("LessonPlanner");
<h1>{t("title")}</h1>
<p>{t("count", { count: n })}</p>                                // ICU: "{count, plural, one {# lesson} other {# lessons}}"
<p>{t.rich("help", { b: (c) => <strong>{c}</strong> })}</p>     // "Press <b>Save</b> when done"
```

Keys are typed from the English catalog, so a misspelled key is a compile
error. Write messages as whole sentences with placeholders. Never join
translated fragments, because word order differs between languages.

**Your own content** (a field that holds `{ "en": "…", "da": "…" }`) resolves
with `resolveLocalized(value, locale)` from `@/i18n`, using the same fallback
rule as the interface: the person's choice → the deployment default → English.

**A screen whose content has its own language** (a lesson written in Danish,
shown to someone whose interface is English) can pin it:
`<LocaleProvider locale="da" syncHtmlLang>…</LocaleProvider>`.

**A different default per person** (by role or profile) is
`setDefaultLocaleResolver((config) => …)` from `@/i18n/userLocale`. An explicit
choice in the switch still wins.

## Skills in your language

A skill's name, description and greeting can be translated in its `SKILL.md`,
next to the original text:

```yaml
display_name: General Assistant
description: >
  Answers general questions.
initial_message: "Hi! How can I help you today?"
locales:
  da:
    display_name: Generel assistent
    initial_message: "Hej! Hvad kan jeg hjælpe med i dag?"
```

- **Only these three fields** can be translated: `display_name`,
  `description` and `initial_message`. Every one is optional. A field a
  language doesn't list shows the original.
- **`initial_message` translates the greeting**, whichever one the skill
  shows: when the skill has a `welcome.intro_message`, a translated
  `initial_message` replaces it for that language.
- **The instructions are not translated.** One set of instructions serves every
  language. A skill that
  needs different instructions per language is a different skill.
- **Which text someone sees:** their language, then that language without its
  region (`pt-BR` → `pt`), then the original. Never the deployment's default
  language: someone who picks English on a Danish deployment sees a skill's
  original English text, not its Danish translation. The original is whatever
  language the author wrote it in, so a fork's own skills can be written in
  Danish with an `en:` block.
- **Checked when the skills are seeded.** Tags are normalised (`pt_br` →
  `pt-BR`). An unknown field, a tag that isn't a language, a value that isn't
  text, or one over its limit (name 128 characters, description 1,024, greeting
  4,000) fails that skill's seed and names the file. Blank values are ignored.
- **`SKILL.md` stays the source** for the template's own skills: a translation
  removed from the file is removed from the live skill on the next deploy.

In **Skill Studio**, a deployment that offers more than one language shows a tab
per language beside **Original**. A language tab edits only those three fields,
shows the original greyed out in each empty one, and saves only what you fill
in. The skills API takes and returns the same `locales` map, and a fork of a
skill keeps its translations.

In your own components, `useSkillText(skill)` from `@/hooks/useSkillText` gives
the three fields in the person's language. The backend's `pick_locale` and
`localized_field` (`backend/i18n.py`) apply the same rules for your own
server-side content.

## Assistants answer in the person's language

Unless your deployment is English-only, every chat run carries the language the
person is reading the app in (`forwardedProps.ui_locale`). The backend adds one
line to the assistant's instructions: reply in that language unless the skill's
own instructions fix a language, or the person writes in or asks for another.
You don't need to configure anything for this. Optionally, set
`UI_LOCALES=da,en` in the backend environment to accept only those tags.

Tools that return localised content can read the person's language from session
state: `tool_context.state.get("ui_locale")` is a tag like `"da"`, or `""` when
there's no preference.

## Help pages in your language

The user guide (`/help`, and the pages the help assistant cites) can be
translated page by page. Put a translation beside your overlay, named for the
locale:

```
docs/customers/<you>/user-guide/<locale>/<slug>.md
```

`<slug>` is a page that exists in English, the template's (`docs/user-guide/`)
or your own. Copy it and translate the text, the `title` and the `summary`;
`audience`, `order` and `requires` carry over when you leave them out.
Screenshots are shared, so keep the `img/…` paths as they are. A page you don't
translate shows in English.

- People see the page in their language once they choose it, falling back along
  the same chain as the interface. The index cards and contents use the
  translated titles.
- With one language configured, the help pages are exactly as before.
- `make guide-check` checks each **bold** button name in a translation against
  your catalog for that locale (and the English interface, for anything not yet
  translated), and checks its links against its own, translated headings.

The full page rules are in `docs/user-guide/README.md` ("Translations").

## What is translated today

The layer, the switch, and the **Settings** screen. Other template screens still
have their English text in the components. They move onto catalogs one area at
a time, in the order recorded in the design. If you need one sooner, move that
area upstream in `platform-source` (strings moved byte for byte, nothing
reworded), then translate it in your folder.
