/**
 * Every semantic colour token a component uses must exist in the Tailwind
 * theme. An undefined one (e.g. `bg-popover` before 2026-10-02) compiles to no
 * CSS at all — no error, no warning — and a menu renders transparent with the
 * text underneath bleeding through. This catches the next one at test time.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import config from "../../tailwind.config";

const SEMANTIC = ["background", "foreground", "card", "popover", "primary", "secondary", "muted", "accent", "destructive"];
const CLASS_RE = new RegExp(`\\b(?:bg|text|border|ring|fill|stroke|from|to|via)-(${SEMANTIC.join("|")})(?:-foreground)?\\b`, "g");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" || name === "node_modules" ? [] : sources(path);
    return /\.(tsx?|jsx?)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

describe("theme colour tokens", () => {
  it("every semantic token used in src/ is defined in tailwind.config", () => {
    const defined = new Set(Object.keys((config.theme?.extend?.colors ?? {}) as Record<string, unknown>));
    const missing = new Map<string, string>();
    for (const file of sources(join(__dirname, ".."))) {
      for (const m of readFileSync(file, "utf8").matchAll(CLASS_RE)) {
        if (!defined.has(m[1]) && !missing.has(m[1])) missing.set(m[1], `${m[0]} in ${file}`);
      }
    }
    expect([...missing.values()]).toEqual([]);
  });
});

describe("floating surfaces", () => {
  it("every Radix menu / popover / tooltip surface uses the shared FLOATING_SURFACE", () => {
    const OPEN_RE = /<(DropdownMenu|Popover|Tooltip|HoverCard|Select|ContextMenu)\.Content\b/g;
    const offenders: string[] = [];
    let found = 0;
    for (const file of sources(join(__dirname, ".."))) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(OPEN_RE)) {
        found += 1;
        // The opening tag's props: up to the first line that closes the tag.
        // (A regex on [^>] stops at the `=>` of an inline handler.)
        const props = text.slice(m.index, m.index + 2000).split(/\n\s*>\s*\n|\/>/)[0];
        if (!/FLOATING_SURFACE|MENU_CONTENT_CLASS/.test(props)) offenders.push(`${m[1]}.Content in ${file}`);
      }
    }
    expect(found).toBeGreaterThanOrEqual(5); // ToolCallChip, DocumentPreview, SkillSwitcher, 2 menus
    expect(offenders).toEqual([]);
  });
});
