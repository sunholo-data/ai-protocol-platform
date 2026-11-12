// A made-up second locale, `xx`, for tests (sprint MULTI-LANG ruling 4). It is
// not a real language and never ships: it proves the machinery — switch,
// fallback, html lang — without implying the template offers any language.

import { GENERATED_CATALOG, type Catalog } from "../messages";

export const XX: Catalog[string] = {
  SettingsPage: { title: "Xettings", navInstructions: "Xinstructions" },
  LanguageSection: { title: "Xanguage" },
  McpTokensSection: { tokenMeta: "· xexpires {expires} · xlast {lastUsed}" },
};

export const FIXTURE_CATALOG: Catalog = { ...GENERATED_CATALOG, xx: XX };
