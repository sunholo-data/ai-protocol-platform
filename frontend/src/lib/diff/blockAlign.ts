// Block-level alignment for side-by-side PPA contract diff (v6.4.0 ONE-DEMO M3).
//
// Two PPA contracts derived from the same template usually share AILANG
// `block_id` values for clauses that the lawyer didn't change. For those,
// alignment is exact and the diff is binary (text-equal vs text-changed).
// For blocks where the block_ids don't match (paragraph numbering shifted,
// clauses inserted/removed), fall back to text-similarity matching with a
// Jaccard token-overlap threshold.
//
// The algorithm is pure-function and isolatable. UI components consume the
// `AlignedRow[]` result and decide how to render added / removed / modified
// / unchanged rows. Tests target this module rather than the React layer
// so the alignment quality risk (Open Q7 in the design doc) gets the most
// thorough coverage with the simplest fixtures.

export interface AlignableBlock {
  block_id?: string;
  text?: string;
  type?: string;
  // We keep an opaque payload — AlignedRow consumers see the full block,
  // including table rows / list items, when they render side-by-side.
  [key: string]: unknown;
}

export type DiffKind = "unchanged" | "modified" | "added" | "removed";

export interface AlignedRow {
  left: AlignableBlock | null;
  right: AlignableBlock | null;
  kind: DiffKind;
  /** 0..1 similarity. 1.0 for byte-identical (`unchanged`); the threshold-passing
   *  value when both sides matched in fallback mode (`modified`); 0 for
   *  added/removed singletons. */
  similarity: number;
}

const DEFAULT_TEXT_SIMILARITY_THRESHOLD = 0.7;

/** Plain-text of an AlignableBlock for similarity comparison.
 *
 * Returns the `.text` field when present, else falls back to a JSON of the
 * block (covers tables / lists / images so they at least participate in
 * the diff rather than being treated as empty). */
function blockText(block: AlignableBlock | null | undefined): string {
  if (!block) return "";
  if (typeof block.text === "string" && block.text.length > 0) return block.text;
  // Best-effort textual representation for richer block types.
  try {
    return JSON.stringify(block).toLowerCase();
  } catch {
    return "";
  }
}

/** Tokenise text into a lowercase Set for Jaccard overlap. Punctuation
 *  stripping keeps Annex/section numbering noise from dominating similarity. */
function tokenize(text: string): Set<string> {
  const tokens = text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1);
  return new Set(tokens);
}

/** Jaccard similarity (|A∩B| / |A∪B|) — 0 when both sets are empty. */
export function jaccardSimilarity(a: string, b: string): number {
  const sa = tokenize(a);
  const sb = tokenize(b);
  if (sa.size === 0 && sb.size === 0) return 0;
  let intersection = 0;
  for (const token of sa) if (sb.has(token)) intersection++;
  const union = sa.size + sb.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

interface AlignOptions {
  /** Jaccard threshold for the text-similarity fallback. Pairs scoring above
   *  this are treated as `modified` matches; below this are surfaced as
   *  `added` (right-only) or `removed` (left-only). Default 0.7. */
  textSimilarityThreshold?: number;
}

/** Align two ordered block lists, preserving document order. The output
 *  preserves the left side's order for blocks that exist on the left; any
 *  right-only blocks are surfaced in their position relative to the
 *  alignment but the consumer can re-order display by document index if
 *  desired.
 *
 *  Strategy:
 *    1. block_id pass: every block with a non-empty block_id on either
 *       side participates in a match against same-block_id on the other.
 *       Matches with identical text → `unchanged`; matches with different
 *       text → `modified` (similarity computed via Jaccard).
 *    2. text-similarity pass: for the remaining unmatched blocks, greedily
 *       pair them by descending Jaccard score, threshold-gated. Above-
 *       threshold pairs → `modified`. Below-threshold left-only → `removed`,
 *       right-only → `added`.
 *
 *  Determinism: the algorithm is fully deterministic given the input order.
 *  No randomness, no LLM. Block_id collisions on the same side are handled
 *  by first-match-wins; downstream UI surfaces these via the row count if
 *  they occur (rare but seen in poorly-keyed corpora). */
export function alignBlocks(
  left: AlignableBlock[],
  right: AlignableBlock[],
  options: AlignOptions = {},
): AlignedRow[] {
  const threshold = options.textSimilarityThreshold ?? DEFAULT_TEXT_SIMILARITY_THRESHOLD;

  const leftMatched = new Array<boolean>(left.length).fill(false);
  const rightMatched = new Array<boolean>(right.length).fill(false);
  const rows: AlignedRow[] = [];

  // Pass 1: block_id alignment
  const rightById = new Map<string, number[]>();
  right.forEach((block, idx) => {
    const id = block.block_id;
    if (typeof id === "string" && id.length > 0) {
      const bucket = rightById.get(id) ?? [];
      bucket.push(idx);
      rightById.set(id, bucket);
    }
  });

  left.forEach((leftBlock, leftIdx) => {
    const id = leftBlock.block_id;
    if (typeof id !== "string" || id.length === 0) return;
    const bucket = rightById.get(id);
    if (!bucket || bucket.length === 0) return;
    const rightIdx = bucket.shift()!;
    const rightBlock = right[rightIdx];
    const leftText = blockText(leftBlock);
    const rightText = blockText(rightBlock);
    const similarity =
      leftText === rightText ? 1 : jaccardSimilarity(leftText, rightText);
    rows.push({
      left: leftBlock,
      right: rightBlock,
      kind: similarity === 1 ? "unchanged" : "modified",
      similarity,
    });
    leftMatched[leftIdx] = true;
    rightMatched[rightIdx] = true;
  });

  // Pass 2: text-similarity fallback for unmatched blocks. Compute all
  // pairwise similarities, sort descending, greedily assign above
  // threshold. O(L*R) — fine for typical PPA size (~hundreds of blocks).
  const unmatchedLeft: number[] = [];
  const unmatchedRight: number[] = [];
  leftMatched.forEach((m, idx) => {
    if (!m) unmatchedLeft.push(idx);
  });
  rightMatched.forEach((m, idx) => {
    if (!m) unmatchedRight.push(idx);
  });

  interface Pair {
    li: number;
    ri: number;
    similarity: number;
  }
  const candidates: Pair[] = [];
  for (const li of unmatchedLeft) {
    const lt = blockText(left[li]);
    for (const ri of unmatchedRight) {
      const sim = jaccardSimilarity(lt, blockText(right[ri]));
      if (sim >= threshold) candidates.push({ li, ri, similarity: sim });
    }
  }
  candidates.sort((a, b) => b.similarity - a.similarity);

  const matchedLeft = new Set<number>();
  const matchedRight = new Set<number>();
  for (const c of candidates) {
    if (matchedLeft.has(c.li) || matchedRight.has(c.ri)) continue;
    matchedLeft.add(c.li);
    matchedRight.add(c.ri);
    rows.push({
      left: left[c.li],
      right: right[c.ri],
      kind: "modified",
      similarity: c.similarity,
    });
  }

  for (const li of unmatchedLeft) {
    if (matchedLeft.has(li)) continue;
    rows.push({ left: left[li], right: null, kind: "removed", similarity: 0 });
  }
  for (const ri of unmatchedRight) {
    if (matchedRight.has(ri)) continue;
    rows.push({ left: null, right: right[ri], kind: "added", similarity: 0 });
  }

  // Restore document order. Rows with a left side use left's index so the
  // sequence of unchanged / modified / removed rows mirrors the left
  // contract's structure. Right-only rows (`added`) slot in AFTER all
  // left-anchored rows, in their right-side order. This is the right
  // call for PPA review — the reviewer is reading down the left contract
  // and wants insertions to surface clearly at the end of their
  // surrounding context rather than risk being mis-positioned by a
  // shared-index sort.
  const keyed = rows.map((row, insertionIdx) => {
    let key: number;
    if (row.left) {
      key = left.indexOf(row.left);
    } else if (row.right) {
      // Push right-only rows beyond left's range, preserving right order.
      key = left.length + right.indexOf(row.right);
    } else {
      key = Number.MAX_SAFE_INTEGER;
    }
    return { row, key, insertionIdx };
  });
  keyed.sort((a, b) => a.key - b.key || a.insertionIdx - b.insertionIdx);
  return keyed.map((k) => k.row);
}
