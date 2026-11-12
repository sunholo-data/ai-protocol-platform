import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { CatalogError, bracesBalanced, build, write } from "./bundle.mjs";

function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "i18n-bundle-"));
  for (const [rel, data] of Object.entries(files)) {
    const p = path.join(root, rel);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, typeof data === "string" ? data : JSON.stringify(data));
  }
  return { templateDir: path.join(root, "messages"), customersDir: path.join(root, "customers"), root };
}

const SOURCE = { "messages/en/settings.json": { SettingsPage: { title: "Settings", hello: "Hi {name}" } } };

test("template only: the source catalog passes through", () => {
  const { catalog, locales, overrides } = build(tree(SOURCE));
  assert.deepEqual(locales, ["en"]);
  assert.equal(catalog.en.SettingsPage.title, "Settings");
  assert.deepEqual(overrides, []);
});

test("an overlay adds a locale for template namespaces", () => {
  const dirs = tree({ ...SOURCE, "customers/acme/messages/xx/settings.json": { SettingsPage: { title: "Xettings" } } });
  const { catalog, locales, overrides } = build(dirs);
  assert.deepEqual(locales, ["en", "xx"]);
  assert.equal(catalog.xx.SettingsPage.title, "Xettings");
  assert.deepEqual(overrides, [], "translating into a new locale is not an override");
});

test("an overlay overriding a template key is listed", () => {
  const dirs = tree({ ...SOURCE, "customers/acme/messages/en/terms.json": { SettingsPage: { title: "Preferences" } } });
  const { catalog, overrides } = build(dirs);
  assert.equal(catalog.en.SettingsPage.title, "Preferences");
  assert.equal(catalog.en.SettingsPage.hello, "Hi {name}", "untouched keys stay");
  assert.deepEqual(overrides, [{ locale: "en", key: "SettingsPage.title", file: path.relative(path.resolve(import.meta.dirname, "../.."), path.join(dirs.customersDir, "acme/messages/en/terms.json")) }]);
});

test("an overlay adds its own namespace", () => {
  const dirs = tree({ ...SOURCE, "customers/acme/messages/en/planner.json": { LessonPlanner: { title: "Plan a lesson" } } });
  const { catalog, added } = build(dirs);
  assert.equal(catalog.en.LessonPlanner.title, "Plan a lesson");
  assert.deepEqual(added, ["LessonPlanner"]);
});

test("a key the template namespace doesn't have is refused", () => {
  const dirs = tree({ ...SOURCE, "customers/acme/messages/xx/settings.json": { SettingsPage: { titel: "typo" } } });
  assert.throws(() => build(dirs), /has no key "titel"/);
});

test("two overlays claiming one new namespace collide", () => {
  const dirs = tree({
    ...SOURCE,
    "customers/a/messages/en/x.json": { Planner: { t: "A" } },
    "customers/b/messages/en/y.json": { Planner: { t: "B" } },
  });
  assert.throws(() => build(dirs), /namespace "Planner" is already defined/);
});

test("one namespace in two template areas collides", () => {
  const dirs = tree({ ...SOURCE, "messages/en/other.json": { SettingsPage: { x: "y" } } });
  assert.throws(() => build(dirs), CatalogError);
});

test("bad input fails loudly", () => {
  assert.throws(() => build(tree({ ...SOURCE, "messages/en/bad.json": "{nope" })), /invalid JSON/);
  assert.throws(() => build(tree({ ...SOURCE, "messages/en/bad.json": { Ns: { k: 1 } } })), /must be a string/);
  assert.throws(() => build(tree({ ...SOURCE, "messages/en/bad.json": { Ns: { k: "Hi {name" } } })), /unbalanced/);
  assert.throws(() => build(tree({ ...SOURCE, "messages/english/x.json": { Ns: { k: "x" } } })), /not a locale tag/);
  assert.throws(() => build(tree({ "messages/xx/x.json": { Ns: { k: "x" } } })), /source catalog/);
});

test("ICU quoting is honoured when checking braces", () => {
  assert.ok(bracesBalanced("Use '{' to open"));
  assert.ok(bracesBalanced("It's {count, plural, one {# file} other {# files}}"));
  assert.ok(!bracesBalanced("Hi {name"));
  assert.ok(!bracesBalanced("Hi }"));
});

test("write puts the source locale first", () => {
  const dirs = tree({ ...SOURCE, "customers/acme/messages/aa/settings.json": { SettingsPage: { title: "A" } } });
  const out = path.join(dirs.root, "out/catalog.json");
  write(build(dirs), out);
  assert.deepEqual(Object.keys(JSON.parse(fs.readFileSync(out, "utf8"))), ["en", "aa"]);
});
