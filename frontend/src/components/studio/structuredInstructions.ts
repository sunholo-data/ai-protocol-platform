// Structured instructions — a guided view over the single markdown `instructions`
// string a skill stores.
//
// The Studio form presents Goal / Guidelines / Constraints / Output format as
// separate labelled fields (v5-style handholding) instead of one blank box.
// `combineInstructions` renders those fields into ## -sectioned markdown;
// `parseInstructions` splits markdown back into fields. Anything that doesn't
// map to a known section (legacy free-form skills, extra headings) is preserved
// verbatim in `additional`, so the round-trip is lossless.
//
// `instructions` remains the single source of truth (the copilot proposes to it
// directly); these helpers are just the editing lens.

export interface StructuredInstructions {
  goal: string;
  guidelines: string;
  constraints: string;
  outputFormat: string;
  /** Preamble + any unrecognised headings, kept verbatim so nothing is lost. */
  additional: string;
}

export const EMPTY_STRUCTURED: StructuredInstructions = {
  goal: "",
  guidelines: "",
  constraints: "",
  outputFormat: "",
  additional: "",
};

type FieldKey = Exclude<keyof StructuredInstructions, "additional">;

// Canonical heading text emitted by combineInstructions.
const HEADINGS: Record<FieldKey, string> = {
  goal: "Goal",
  guidelines: "Guidelines",
  constraints: "Constraints",
  outputFormat: "Output format",
};

// Recognised heading titles (lowercased) → field key. Aliases let us re-absorb
// headings a human or the copilot might have written slightly differently.
const TITLE_TO_KEY: Record<string, FieldKey> = {
  goal: "goal",
  purpose: "goal",
  guidelines: "guidelines",
  behaviour: "guidelines",
  behavior: "guidelines",
  "guidelines / behaviour": "guidelines",
  constraints: "constraints",
  "constraints & guidelines": "constraints",
  limitations: "constraints",
  "output format": "outputFormat",
  output: "outputFormat",
  "response format": "outputFormat",
};

const HEADING_RE = /^#{1,6}\s+(.+?)\s*$/;

/** Split markdown instructions into the structured fields. Lossless: unmatched
 * content lands in `additional`. */
export function parseInstructions(markdown: string): StructuredInstructions {
  const buckets: Record<keyof StructuredInstructions, string[]> = {
    goal: [],
    guidelines: [],
    constraints: [],
    outputFormat: [],
    additional: [],
  };

  let current: keyof StructuredInstructions = "additional";
  for (const line of (markdown ?? "").split("\n")) {
    const m = line.match(HEADING_RE);
    if (m) {
      const key = TITLE_TO_KEY[m[1].trim().toLowerCase()];
      if (key) {
        current = key;
        continue; // drop the heading itself; content flows into the bucket
      }
      // Unrecognised heading — keep it (and following content) in additional.
      current = "additional";
      buckets.additional.push(line);
      continue;
    }
    buckets[current].push(line);
  }

  return {
    goal: buckets.goal.join("\n").trim(),
    guidelines: buckets.guidelines.join("\n").trim(),
    constraints: buckets.constraints.join("\n").trim(),
    outputFormat: buckets.outputFormat.join("\n").trim(),
    additional: buckets.additional.join("\n").trim(),
  };
}

/** Render the structured fields back into ## -sectioned markdown. Empty
 * sections are omitted. `additional` is appended verbatim. */
export function combineInstructions(fields: StructuredInstructions): string {
  const parts: string[] = [];
  (Object.keys(HEADINGS) as FieldKey[]).forEach((key) => {
    const body = fields[key]?.trim();
    if (body) parts.push(`## ${HEADINGS[key]}\n\n${body}`);
  });
  const additional = fields.additional?.trim();
  if (additional) parts.push(additional);
  return parts.join("\n\n");
}
