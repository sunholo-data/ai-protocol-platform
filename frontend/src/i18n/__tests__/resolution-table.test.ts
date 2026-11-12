// The ONE resolution table, shared with the backend: pytest runs the same cases
// against backend/i18n.py (tests/unit/test_i18n.py), so resolveLocalized /
// localizedField and pick_locale / localized_field cannot drift apart.

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { localizedField, resolveLocalized, type LocaleVariants, type Localized } from "..";

interface Case {
  name: string;
  fn: "pick" | "field";
  value?: Localized;
  base?: string | null;
  variants?: LocaleVariants | null;
  field?: string;
  locale: string;
  default?: string;
  expect: string;
}

// vitest runs from frontend/; the table lives with the backend's fixtures.
const TABLE = path.resolve(process.cwd(), "../backend/tests/fixtures/i18n_resolution.json");
const cases = (JSON.parse(readFileSync(TABLE, "utf8")) as { cases: Case[] }).cases;

describe("shared resolution table (backend/tests/fixtures/i18n_resolution.json)", () => {
  it("has both shapes", () => {
    expect(new Set(cases.map((c) => c.fn))).toEqual(new Set(["pick", "field"]));
  });

  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const got =
      c.fn === "pick"
        ? resolveLocalized(c.value, c.locale, c.default ?? "en")
        : localizedField(c.base, c.variants, c.field ?? "", c.locale);
    expect(got).toBe(c.expect);
  });
});
