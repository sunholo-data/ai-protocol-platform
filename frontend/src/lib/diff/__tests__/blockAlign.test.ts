import { describe, expect, it } from "vitest";

import { alignBlocks, jaccardSimilarity, type AlignableBlock } from "@/lib/diff/blockAlign";

function block(text: string, block_id?: string, type = "paragraph"): AlignableBlock {
  return { text, block_id, type };
}

describe("jaccardSimilarity", () => {
  it("returns 1 for identical text", () => {
    expect(jaccardSimilarity("Pay as Produced settlement", "Pay as Produced settlement")).toBe(1);
  });

  it("returns 0 for fully disjoint text", () => {
    expect(jaccardSimilarity("alpha bravo charlie", "delta echo foxtrot")).toBe(0);
  });

  it("ignores punctuation differences", () => {
    expect(jaccardSimilarity("Section 4.2 — Payment terms", "Section 4.2: Payment terms")).toBeCloseTo(1);
  });

  it("returns 0 when both strings empty", () => {
    expect(jaccardSimilarity("", "")).toBe(0);
  });

  it("scores moderate overlap somewhere in (0, 1)", () => {
    const score = jaccardSimilarity(
      "The Buyer shall pay the Seller for each MWh produced",
      "The Buyer shall remit the Seller for each MWh delivered",
    );
    expect(score).toBeGreaterThan(0);
    expect(score).toBeLessThan(1);
  });
});

describe("alignBlocks — block_id matching", () => {
  it("marks identical-text same-id blocks as unchanged", () => {
    const left = [block("Definitions clause", "blk-1"), block("Settlement clause", "blk-2")];
    const right = [block("Definitions clause", "blk-1"), block("Settlement clause", "blk-2")];
    const rows = alignBlocks(left, right);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.kind === "unchanged" && r.similarity === 1)).toBe(true);
  });

  it("marks same-id text-changed blocks as modified with similarity in (0, 1)", () => {
    const left = [block("The Buyer shall pay €45 per MWh", "blk-price")];
    const right = [block("The Buyer shall pay CPI-indexed price per MWh", "blk-price")];
    const rows = alignBlocks(left, right);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("modified");
    expect(rows[0].similarity).toBeGreaterThan(0);
    expect(rows[0].similarity).toBeLessThan(1);
    expect(rows[0].left).toBe(left[0]);
    expect(rows[0].right).toBe(right[0]);
  });

  it("handles right-only block (added) when block_id present on right only", () => {
    const left = [block("Definitions", "blk-def")];
    const right = [
      block("Definitions", "blk-def"),
      block("New change-of-law clause", "blk-cof"),
    ];
    const rows = alignBlocks(left, right);
    const added = rows.filter((r) => r.kind === "added");
    expect(added).toHaveLength(1);
    expect(added[0].right?.text).toBe("New change-of-law clause");
  });
});

describe("alignBlocks — text-similarity fallback", () => {
  it("pairs unmatched-id blocks above the Jaccard threshold as modified", () => {
    // No block_ids; text is highly similar
    const left = [block("The Buyer shall pay €45 per MWh of energy produced")];
    const right = [block("The Buyer shall remit €45 per MWh of energy generated")];
    const rows = alignBlocks(left, right, { textSimilarityThreshold: 0.5 });
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("modified");
    expect(rows[0].similarity).toBeGreaterThanOrEqual(0.5);
  });

  it("surfaces below-threshold pairs as added + removed singletons (no false-positive merge)", () => {
    // No id, low text overlap — must NOT be merged
    const left = [block("Settlement type shall be Pay-as-Produced")];
    const right = [block("Quarterly mark-to-market hedging mechanism applies")];
    const rows = alignBlocks(left, right, { textSimilarityThreshold: 0.5 });
    expect(rows).toHaveLength(2);
    expect(rows.some((r) => r.kind === "removed" && r.left?.text?.includes("Pay-as-Produced"))).toBe(
      true,
    );
    expect(rows.some((r) => r.kind === "added" && r.right?.text?.includes("Quarterly"))).toBe(true);
  });

  it("mixes block_id matches and text-similarity matches in document order", () => {
    const left = [
      block("Definitions section", "blk-def"),
      block("The Buyer shall pay €45 per MWh"),
      block("Term of 20 years"),
    ];
    const right = [
      block("Definitions section", "blk-def"),
      block("The Buyer shall remit €45 per MWh"),
      block("Term of 20 years"),
    ];
    const rows = alignBlocks(left, right, { textSimilarityThreshold: 0.5 });
    expect(rows).toHaveLength(3);
    expect(rows[0].kind).toBe("unchanged");
    expect(rows[1].kind).toBe("modified");
    expect(rows[2].kind).toBe("modified"); // identical text but no block_id → text-similarity path
    expect(rows[2].similarity).toBe(1);
  });

  it("threshold gates the fallback — high threshold rejects more pairs as added/removed", () => {
    const left = [block("The Buyer pays the Seller for each MWh delivered")];
    const right = [block("The Seller delivers MWh to the Buyer in exchange for payment")];
    // Below 0.95 — they share several tokens but the Jaccard isn't that high
    const lenientRows = alignBlocks(left, right, { textSimilarityThreshold: 0.3 });
    const strictRows = alignBlocks(left, right, { textSimilarityThreshold: 0.95 });
    expect(lenientRows.some((r) => r.kind === "modified")).toBe(true);
    expect(strictRows.every((r) => r.kind === "added" || r.kind === "removed")).toBe(true);
  });

  it("preserves document order of left when intermixing matched and unmatched rows", () => {
    const left = [
      block("Clause A — definitions", "blk-A"),
      block("Clause B — unique to left"),
      block("Clause C — settlement", "blk-C"),
    ];
    const right = [block("Clause A — definitions", "blk-A"), block("Clause C — settlement", "blk-C")];
    const rows = alignBlocks(left, right);

    // Expected order: A (unchanged), B (removed), C (unchanged)
    expect(rows).toHaveLength(3);
    expect(rows[0].left?.block_id).toBe("blk-A");
    expect(rows[1].kind).toBe("removed");
    expect(rows[1].left?.text).toContain("unique to left");
    expect(rows[2].left?.block_id).toBe("blk-C");
  });
});
