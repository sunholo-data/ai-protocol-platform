import { createLowlight } from "lowlight";
import { describe, expect, it } from "vitest";
import { ailang } from "@/lib/highlight/ailang";

/** Flatten a lowlight tree into [className, text] pairs for the scoped tokens. */
function tokens(code: string): Array<[string, string]> {
  const lowlight = createLowlight({ ailang });
  const out: Array<[string, string]> = [];
  const text = (n: { type: string; value?: string; children?: unknown[] }): string =>
    n.type === "text" ? (n.value ?? "") : (n.children ?? []).map((c) => text(c as never)).join("");
  const walk = (n: { type: string; properties?: { className?: string[] }; children?: unknown[] }) => {
    if (n.type === "element" && n.properties?.className) out.push([n.properties.className.join(" "), text(n as never)]);
    (n.children ?? []).forEach((c) => walk(c as never));
  };
  walk(lowlight.highlight("ailang", code) as never);
  return out;
}

const SAMPLE = `module examples/demo
import std/io (println)

-- Greets someone, with a contract
export func greet(name: string) -> string ! {IO, FS}
  requires length(name) > 0
{
  let msg = "hi " ++ name in
  if true then println(msg) else _io_print(msg)
}

type Shape = Circle(float) | Square(float)
`;

describe("AILANG highlighting", () => {
  const t = tokens(SAMPLE);
  const has = (cls: string, txt: string) => t.some(([c, s]) => c.includes(cls) && s.includes(txt));

  it("marks keywords, including the contract keywords the editor grammar lacks", () => {
    for (const kw of ["module", "import", "export", "func", "let", "in", "if", "then", "else", "type", "requires"]) {
      expect(has("hljs-keyword", kw), kw).toBe(true);
    }
  });

  it("marks comments, strings, literals and builtins", () => {
    expect(has("hljs-comment", "Greets someone")).toBe(true);
    expect(has("hljs-string", '"hi "')).toBe(true);
    expect(has("hljs-literal", "true")).toBe(true);
    expect(has("hljs-built_in", "_io_print")).toBe(true);
  });

  it("marks function names, types and the effect row", () => {
    expect(has("hljs-title function_", "greet")).toBe(true);
    expect(has("hljs-type", "Shape")).toBe(true);
    expect(has("hljs-meta", "IO, FS")).toBe(true);
  });

  it("is registered under the .ail alias", () => {
    const lowlight = createLowlight({ ailang });
    expect(lowlight.registered("ail")).toBe(true);
  });
});
