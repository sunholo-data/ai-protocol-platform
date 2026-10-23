import type { LanguageFn } from "lowlight";

/**
 * AILANG for highlight.js (via lowlight / rehype-highlight), so ```ailang and
 * ```ail fences in chat are coloured like every other language.
 *
 * Sources, and which one wins:
 *  - Keywords: the lexer's own table (ailang internal/lexer/token.go). The
 *    editor grammar (syntaxes/ailang.tmLanguage.json) lags it — it has no
 *    `requires`/`ensures`/`invariant`/`deriving`/`and`/`or`.
 *  - Everything else follows the editor grammar: `--` and `//` comments,
 *    capitalised identifiers as types, `_name` builtins, `func name` titles.
 *  - Effect rows (`! {IO, FS}`) get their own scope — effects are what makes a
 *    signature read as AILANG, so they are worth picking out.
 */
export const ailang: LanguageFn = (hljs) => ({
  name: "AILANG",
  aliases: ["ail"],
  keywords: {
    keyword: [
      "func", "pure", "let", "letrec", "in", "if", "then", "else", "match", "with",
      "type", "class", "instance", "module", "import", "export", "extern", "as",
      "forall", "exists", "deriving",
      "requires", "ensures", "invariant",
      "test", "tests", "property", "properties", "assert",
      "spawn", "parallel", "select", "channel", "send", "recv", "timeout",
      "not", "and", "or",
    ],
    literal: ["true", "false"],
    type: ["int", "float", "bool", "string", "char", "unit"],
    built_in: ["print", "println", "show", "intToFloat", "floatToInt"],
  },
  contains: [
    hljs.COMMENT("--", "$"),
    hljs.C_LINE_COMMENT_MODE,
    { scope: "string", begin: '"', end: '"', contains: [hljs.BACKSLASH_ESCAPE] },
    { scope: "string", match: /'(?:[^'\\]|\\[nrt0'\\])'/ },
    { scope: "number", match: /\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+(?:[eE][+-]?\d+)?)?)\b/ },
    // `! {IO, FS}` — the effect row of a signature.
    {
      scope: "meta",
      begin: /!\s*\{/,
      end: /\}/,
      contains: [{ scope: "type", match: /\b[A-Z][A-Za-z0-9]*\b/ }],
    },
    // `func name` / `extern func name` / `export func name`
    {
      match: [/\bfunc/, /\s+/, /[a-z_][A-Za-z0-9_]*/],
      scope: { 1: "keyword", 3: "title.function" },
    },
    // `module a/b`, `import std/io (println)`
    {
      match: [/\b(?:module|import)/, /\s+/, /[A-Za-z][A-Za-z0-9_/]*/],
      scope: { 1: "keyword", 3: "title.class" },
    },
    { scope: "built_in", match: /\b_[a-z][A-Za-z0-9_]*\b/ },
    { scope: "type", match: /\b[A-Z][A-Za-z0-9]*\b/ },
    { scope: "operator", match: /=>|->|<-|::|\+\+|==|!=|<=|>=|&&|\|\||\\/ },
  ],
});
