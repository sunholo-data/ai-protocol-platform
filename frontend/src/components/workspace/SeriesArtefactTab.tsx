// SeriesArtefactTab — the workbench Result tab for a DECLARED SERIES surface
// (PRICES-WORKSPACE M2, docs/design/v6.12.0/market-prices-workspace.md).
//
// The backend result→A2UI transform (adk/a2ui_entsoe_render.py) renders a Basic
// v0.9 SUMMARY card as the component tree (Basic has no chart), and stashes the
// full series in the surface data model at "/" as a declared series envelope:
//
//   { kind: "series", title, x: {key,label,type}, y: [{key,label,unit}],
//     rows: [...], stats: {avg,min,max}, sourceUri,
//     biddingZone, startDate, endDate, rowCount }
//
// This tab reads that and charts it. It holds NO domain knowledge of ENTSO-E or
// PPAs: the axes are read from `x`/`y`, so a second dataset-shaped tool (load /
// solar / wind, `captured_rates`) reuses this tab by declaring `x`/`y` only —
// that's the whole point of the envelope. Never hardcode a column name here.
//
// Same split as the Clauses / Sources tabs: component tree = generic fallback,
// data model = the rich render.
//
// Three things here are load-bearing, each from a real incident:
//   - Stats come from the server (`stats`) and are NEVER re-derived client-side.
//     The agent quotes the same numbers in prose; two derivations would drift.
//   - The rendered date range is ALWAYS visible (citation chip), including in
//     the empty states — a wrong range (asked 2026, got 2024) shipped silently
//     to a user this week. A wrong range must be *visible*, not silent.
//   - Empty / all-null (unsettled) ranges render an explicit notice, never a
//     blank pane (NEVER SILENT, CLAUDE.md #8).

"use client";

import { useMemo, useRef, useState } from "react";
import { useSurfaceState } from "@/providers/SurfaceRegistry";

// ── The declared envelope ────────────────────────────────────────────────────

interface AxisDecl {
  key?: string;
  label?: string;
  type?: string;
  unit?: string;
}

/** An uncertainty band around a series — the two edge columns are read from the
 *  same rows, so a band costs no extra payload. Declared by the server (ONE's
 *  `_basecase`/`_lowcase`/`_highcase` convention); the tab holds no knowledge of
 *  that convention, exactly as it holds none of ENTSO-E's. */
interface BandDecl {
  key?: string;
  lower?: string;
  upper?: string;
  label?: string;
}

interface SeriesRoot {
  kind?: string;
  title?: string;
  x?: AxisDecl;
  y?: AxisDecl[];
  rows?: Record<string, unknown>[];
  stats?: { avg?: number | null; min?: number | null; max?: number | null } | null;
  sourceUri?: string;
  startDate?: string;
  endDate?: string;
  rowCount?: number;
  /** ONE-BQ-SHAPES — all optional; absent means "render as before". */
  bands?: BandDecl[];
  /** Measures the server declined to chart because they are on another scale.
   *  Declared, never dropped: the tab must be able to say what it left out. */
  deferred?: AxisDecl[];
  /** "percent" renders a dimensionless ratio as a percentage. */
  yFormat?: string;
  /** A horizontal reference line (1.0 for a capture rate / seasonal index). */
  reference?: { value?: number; label?: string } | null;
}

// ── Series colours ───────────────────────────────────────────────────────────
// Fixed-order categorical slots (never cycled, never assigned by rank) from the
// validated default palette, stepped per mode. Declared as CSS custom properties
// so light/dark swap in one place and the SVG is written against roles. Written
// as a static literal so Tailwind's JIT scanner sees the arbitrary properties.
// Validated as a set: light worst adjacent CVD ΔE 24.2; dark 10.3 (floor band —
// legal because ≥2 series always ship a legend, and M3 adds the table view).
const SERIES_VARS =
  "[--series-1:#2a78d6] dark:[--series-1:#3987e5] " +
  "[--series-2:#1baf7a] dark:[--series-2:#199e70] " +
  "[--series-3:#eda100] dark:[--series-3:#c98500] " +
  "[--series-4:#008300] dark:[--series-4:#008300] " +
  "[--series-5:#4a3aa7] dark:[--series-5:#9085e9] " +
  "[--series-6:#e34948] dark:[--series-6:#e66767] " +
  "[--series-7:#e87ba4] dark:[--series-7:#d55181] " +
  "[--series-8:#eb6834] dark:[--series-8:#d95926]";

const SLOT_COUNT = 8;

function slotVar(i: number): string {
  return `var(--series-${(i % SLOT_COUNT) + 1})`;
}

// ── Value coercion ───────────────────────────────────────────────────────────

function toNum(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** x → a number for positioning. `type: "time"` parses ISO; otherwise numeric.
 *  Unparseable (e.g. a category axis) → null, and the row index is used. */
function toX(v: unknown, type?: string): number | null {
  if (type === "time") {
    const t = Date.parse(String(v ?? ""));
    return Number.isFinite(t) ? t : null;
  }
  return toNum(v);
}

const NUM = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const PCT = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** A capture rate of 0.729 is read as "73%", never as "0.73 EUR/MWh". When the
 *  server declares `yFormat: "percent"` the unit is meaningless by construction
 *  (the measure is dimensionless), so it is dropped rather than appended. */
function fmtValue(v: number | null | undefined, unit?: string, format?: string): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (format === "percent") return `${PCT.format(v * 100)}%`;
  return unit ? `${NUM.format(v)} ${unit}` : NUM.format(v);
}

/** Compact axis tick text — no unit, no fixed decimals for round numbers. */
function fmtTick(v: number, format?: string): string {
  if (format === "percent") {
    const pct = v * 100;
    return `${Number.isInteger(pct) ? pct : Math.round(pct * 10) / 10}%`;
  }
  const abs = Math.abs(v);
  if (abs >= 1000) return `${Math.round(v / 100) / 10}k`;
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
}

/** Deterministic, UTC, locale-independent x labels (a locale formatter makes
 *  tests flaky and the label ambiguous across the reader's timezone). */
function fmtX(v: number, type: string | undefined, spanMs: number): string {
  if (type !== "time") return fmtTick(v);
  const d = new Date(v);
  const iso = d.toISOString();
  if (spanMs > 3 * 24 * 3600 * 1000) return iso.slice(5, 10); // MM-DD
  return iso.slice(11, 16); // HH:mm
}

/** Nice round tick values across [min,max]. */
function niceTicks(min: number, max: number, count = 4): number[] {
  if (!(max > min)) return [min];
  const step0 = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) {
    out.push(Number(v.toFixed(10)));
  }
  return out;
}

/** Only render a source we can vouch for as a friendly, non-confidential ref
 *  (CLAUDE.md #9). `gs://` paths, doc-ids and artifact ids are backend
 *  addressing and must never reach the screen — anything unrecognised is
 *  dropped rather than shown raw. */
function safeSource(uri: unknown): string | null {
  const u = String(uri ?? "").trim();
  if (!u) return null;
  if (/^(bq|https?):\/\//.test(u)) return u;
  return null;
}

// ── Chart geometry ───────────────────────────────────────────────────────────

const W = 720;
const H = 260;
const M = { top: 12, right: 16, bottom: 26, left: 54 };
const PLOT_W = W - M.left - M.right;
const PLOT_H = H - M.top - M.bottom;

interface Series {
  key: string;
  label: string;
  unit?: string;
  color: string;
  values: (number | null)[];
}

// ── Table: columns from the declared axes, sort, CSV (M3) ──────────────────
// Same rule as the chart above: columns are read from `x`/`y`, never a
// hardcoded name, so the alternate-columns fixture (wind_mw/solar_mw) works
// with zero code change here too.

interface ColumnDecl {
  key: string;
  label: string;
  unit?: string;
  type?: string;
}

/** Coerce a raw cell value into something sortable: a number, a string, or
 *  null. `null` is the single signal that means "sorts last" — a raw empty
 *  string or unparseable value collapses to it too, since neither has a
 *  meaningful position relative to real values. */
function sortValue(v: unknown, type?: string): number | string | null {
  if (v == null) return null;
  if (type === "time") {
    const t = Date.parse(String(v));
    return Number.isFinite(t) ? t : null;
  }
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    const s = v.trim();
    if (s === "") return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : s;
  }
  return null;
}

/** Nulls sort last regardless of direction — a null return happens BEFORE
 *  the asc/desc flip below, so it can never be reordered by clicking twice. */
function compareRows(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  key: string,
  type: string | undefined,
  dir: "asc" | "desc",
): number {
  const av = sortValue(a[key], type);
  const bv = sortValue(b[key], type);
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;
  const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
  return dir === "asc" ? cmp : -cmp;
}

/** Cell text for the table — formatted for reading (units, "—" for null). */
function fmtCell(v: unknown, col: ColumnDecl): string {
  if (v == null) return "—";
  if (col.type === "time") {
    const t = Date.parse(String(v));
    if (Number.isFinite(t)) return `${new Date(t).toISOString().slice(0, 19).replace("T", " ")} UTC`;
  }
  const n = toNum(v);
  if (n != null) return fmtValue(n, col.unit);
  const s = String(v).trim();
  return s === "" ? "—" : s;
}

/** RFC 4180 escaping — quote a field that contains a comma, quote, or
 *  newline, doubling any embedded quotes. */
function csvCell(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV of the given rows under the given columns — header from the declared
 *  labels, values as-is (no unit suffix, no null→"—" reformatting; a
 *  spreadsheet gets the raw number/string, not a display string). Exported
 *  so sort/escaping edge cases are unit-testable without a DOM download. */
export function rowsToCsv(columns: ColumnDecl[], rows: Record<string, unknown>[]): string {
  const header = columns.map((c) => csvCell(c.label)).join(",");
  const body = rows.map((r) => columns.map((c) => csvCell(r[c.key])).join(","));
  return [header, ...body].join("\r\n");
}

function csvFilename(title: string): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
  return slug || "series";
}

// ── Component ────────────────────────────────────────────────────────────────

export function SeriesArtefactTab({ surfaceId, className }: { surfaceId: string; className?: string }) {
  const state = useSurfaceState(surfaceId);
  const root = (state?.surface?.dataModel?.get("/") ?? null) as SeriesRoot | null;
  const [hover, setHover] = useState<number | null>(null);

  const rows = useMemo(() => (Array.isArray(root?.rows) ? root.rows : []), [root]);
  const xDecl = root?.x;
  const yDecls = useMemo(() => (Array.isArray(root?.y) ? root.y.filter((d) => d?.key) : []), [root]);

  // Every value is read through the DECLARED axes — never a hardcoded column.
  const series: Series[] = useMemo(
    () =>
      yDecls.map((d, i) => ({
        key: String(d.key),
        label: d.label || String(d.key),
        unit: d.unit,
        color: slotVar(i),
        values: rows.map((r) => toNum(r[String(d.key)])),
      })),
    [yDecls, rows],
  );

  const xs = useMemo(() => rows.map((r, i) => toX(r[String(xDecl?.key ?? "")], xDecl?.type) ?? i), [rows, xDecl]);

  // A band borrows its OWNING series' colour rather than taking a palette slot —
  // it is the same entity's uncertainty, not a ninth series (that distinction is
  // the whole reason nine captured-price columns fit in an eight-slot palette).
  const bands = useMemo(() => {
    const decls = Array.isArray(root?.bands) ? root.bands : [];
    return decls
      .filter((b) => b?.key && b?.lower && b?.upper)
      .map((b) => {
        const owner = yDecls.findIndex((d) => String(d.key) === String(b.key));
        return {
          key: String(b.key),
          color: slotVar(owner < 0 ? 0 : owner),
          lower: rows.map((r) => toNum(r[String(b.lower)])),
          upper: rows.map((r) => toNum(r[String(b.upper)])),
        };
      });
  }, [root, yDecls, rows]);

  const chart = useMemo(() => {
    // Band edges join the domain, or the band clips against the plot frame and
    // reads as though the forecast were narrower than it is.
    const finite = [
      ...series.flatMap((s) => s.values.filter((v): v is number => v != null)),
      ...bands.flatMap((b) => [...b.lower, ...b.upper].filter((v): v is number => v != null)),
    ];
    if (!finite.length || xs.length < 1) return null;

    let lo = Math.min(...finite);
    let hi = Math.max(...finite);
    if (lo === hi) {
      // A flat series still deserves a readable band.
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;

    const xLo = Math.min(...xs);
    const xHi = Math.max(...xs);
    const xSpan = xHi - xLo || 1;

    const px = (x: number) => M.left + ((x - xLo) / xSpan) * PLOT_W;
    const py = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * PLOT_H;

    // Negative values are real and analytically important (−7.6 EUR/MWh was
    // observed live), so the zero line is drawn whenever it's in view — a dip
    // below it must be legible AS a dip, not just a low point on a curve.
    const zeroInView = lo <= 0 && hi >= 0;

    return {
      lo,
      hi,
      xLo,
      xSpan,
      px,
      py,
      zeroInView,
      zeroY: zeroInView ? py(0) : null,
      yTicks: niceTicks(lo, hi, 4),
      // Filled area between the two edges, in segments so a gap in either edge
      // breaks the band rather than bridging across missing data.
      areas: bands.map((b) => {
        const segments: string[] = [];
        let run: number[] = [];
        const flush = () => {
          if (run.length >= 2) {
            const top = run.map((i) => `${px(xs[i]).toFixed(2)} ${py(b.upper[i] as number).toFixed(2)}`);
            const bottom = [...run]
              .reverse()
              .map((i) => `${px(xs[i]).toFixed(2)} ${py(b.lower[i] as number).toFixed(2)}`);
            segments.push(`M${top.join("L")}L${bottom.join("L")}Z`);
          }
          run = [];
        };
        b.lower.forEach((low, i) => {
          if (low == null || b.upper[i] == null) flush();
          else run.push(i);
        });
        flush();
        return { key: b.key, color: b.color, d: segments.join("") };
      }),
      paths: series.map((s) => {
        // Break the line at nulls (an unsettled tail) rather than bridging a gap
        // that would imply data we don't have.
        let d = "";
        let pen = false;
        s.values.forEach((v, i) => {
          if (v == null) {
            pen = false;
            return;
          }
          d += `${pen ? "L" : "M"}${px(xs[i]).toFixed(2)} ${py(v).toFixed(2)}`;
          pen = true;
        });
        return { ...s, d };
      }),
    };
  }, [series, xs, bands]);

  const yFormat = typeof root?.yFormat === "string" ? root.yFormat : undefined;
  const refValue = typeof root?.reference?.value === "number" ? root.reference.value : null;
  const refLabel = String(root?.reference?.label ?? "").trim();
  // Declared but not charted, because they sit on another scale. Shown as a
  // notice, never silently dropped (CLAUDE.md #8). Memoised because `columns`
  // depends on it — a fresh array each render would defeat that memo.
  const deferred = useMemo(
    () => (Array.isArray(root?.deferred) ? root.deferred.filter((d) => d?.key) : []),
    [root],
  );

  // ── Table state — must sit above the empty-state early returns below, same
  // as every other hook in this component (React's hooks-must-run-every-
  // render rule; the guards use `root?.…` so they're safe pre-data too).
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [scrollTop, setScrollTop] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // The table is the COMPLETE result, not the chart's subset: a band's low/high
  // edges and any measure deferred off the axis are still columns here and in
  // the CSV. Collapsing nine measures into three lines is a reading decision —
  // it must never cost the user access to the numbers.
  const columns: ColumnDecl[] = useMemo(() => {
    const cols: ColumnDecl[] = [];
    const seen = new Set<string>();
    const push = (key: string, label: string, extra: Partial<ColumnDecl> = {}) => {
      if (!key || seen.has(key)) return;
      seen.add(key);
      cols.push({ key, label, ...extra });
    };
    if (xDecl?.key) push(String(xDecl.key), xDecl.label || String(xDecl.key), { type: xDecl.type });
    for (const d of yDecls) push(String(d.key), d.label || String(d.key), { unit: d.unit });
    for (const b of Array.isArray(root?.bands) ? root.bands : []) {
      if (b?.lower) push(String(b.lower), String(b.lower));
      if (b?.upper) push(String(b.upper), String(b.upper));
    }
    for (const d of deferred) push(String(d.key), d.label || String(d.key), { unit: d.unit });
    return cols;
  }, [xDecl, yDecls, root, deferred]);

  const sortedRows = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    return rows
      .map((r, i) => ({ r, i }))
      .sort((a, b) => {
        const cmp = compareRows(a.r, b.r, sortKey, col?.type, sortDir);
        return cmp !== 0 ? cmp : a.i - b.i; // stable: tie-break on original order
      })
      .map((x) => x.r);
  }, [rows, sortKey, sortDir, columns]);

  const stats = root?.stats ?? null;
  const unit = yDecls[0]?.unit;
  const source = safeSource(root?.sourceUri);
  const start = String(root?.startDate ?? "").trim();
  const end = String(root?.endDate ?? "").trim();
  const range = start && end ? `${start} → ${end}` : "";
  const title = String(root?.title ?? "").trim() || "Series";
  const rowCount = typeof root?.rowCount === "number" ? root.rowCount : rows.length;
  // The server caps the series at `_MAX_ROWS` (1000). When the true count
  // exceeds what we were sent, say so explicitly (NEVER SILENT, CLAUDE.md #8)
  // rather than let the table quietly show a subset with no indication.
  const capped = rowCount > rows.length;

  function toggleSort(key: string) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
    setScrollTop(0);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }

  function handleDownloadCsv() {
    const csv = rowsToCsv(columns, sortedRows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${csvFilename(title)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Fixed-height virtualisation: the scroll container's height is a CSS
  // constant (`max-h-80` = 320px below), not a measured DOM value, so the
  // visible window is computed from that constant rather than
  // getBoundingClientRect/ResizeObserver. That keeps it deterministic in
  // jsdom (which never lays out, so a measured height would silently be 0)
  // and avoids a layout-measurement effect entirely. At the 1000-row cap this
  // renders ~24 <tr> at a time instead of up to 1000 — DOM stays small without
  // a virtualisation library.
  const ROW_H = 28;
  const VIEWPORT_H = 320;
  const OVERSCAN = 6;
  const visibleCount = Math.ceil(VIEWPORT_H / ROW_H) + OVERSCAN * 2;
  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const endIndex = Math.min(sortedRows.length, startIndex + visibleCount);
  const windowRows = sortedRows.slice(startIndex, endIndex);
  const topPad = startIndex * ROW_H;
  const bottomPad = (sortedRows.length - endIndex) * ROW_H;

  // The citation chip renders in EVERY state (including empty) — a wrong range
  // must be visible, and "nothing here" is only meaningful next to "for what".
  const citation =
    source || range ? (
      <div
        data-testid="series-citation"
        className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 py-2 text-xs text-muted-foreground"
      >
        {range && (
          <span className="rounded-md border border-border bg-muted/30 px-2 py-0.5 font-medium text-foreground">
            {range}
          </span>
        )}
        {rowCount > 0 && <span>{rowCount.toLocaleString("en-US")} rows</span>}
        {source && (
          <>
            <span aria-hidden="true">·</span>
            <span className="truncate font-mono text-[11px]" title={source}>
              {source}
            </span>
          </>
        )}
      </div>
    ) : null;

  const header = (
    <div className="sticky top-0 z-10 border-b border-border bg-background/95 px-4 py-3 backdrop-blur">
      <h2 className="text-sm font-semibold tracking-tight text-foreground">{title}</h2>
      {xDecl?.label && yDecls.length > 0 && (
        <p className="truncate text-xs text-muted-foreground">
          {yDecls.map((d) => d.label || d.key).join(" · ")}
          {unit ? ` (${unit})` : ""} by {xDecl.label}
        </p>
      )}
    </div>
  );

  // ── Degraded states — never a blank pane (CLAUDE.md #8) ────────────────────
  if (!root || (!rows.length && !yDecls.length)) {
    return (
      <div className={`flex h-full flex-col overflow-auto ${className ?? ""}`}>
        {header}
        <p data-testid="series-empty" className="p-4 text-sm text-muted-foreground">
          No data has been returned for this query yet.
        </p>
      </div>
    );
  }

  if (!chart) {
    // Rows present but every declared value is null (an unsettled range), or no
    // rows at all. Both are a real answer — say so, and show the range it's an
    // answer ABOUT.
    return (
      <div className={`flex h-full flex-col overflow-auto ${className ?? ""}`}>
        {header}
        {citation}
        <div data-testid="series-empty" className="px-4 py-3">
          <p className="text-sm font-medium text-foreground">No settled prices in this range.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {rows.length > 0
              ? `${rows.length.toLocaleString("en-US")} rows were returned, but none carry a settled value — the range may not be settled yet. Try an earlier range.`
              : "The query returned no rows. Check the range and try again."}
          </p>
        </div>
      </div>
    );
  }

  const hoverRow = hover != null && hover >= 0 && hover < xs.length ? hover : null;

  return (
    <div className={`flex h-full flex-col overflow-auto ${SERIES_VARS} ${className ?? ""}`}>
      {header}

      {/* Stat row — server-computed values ONLY. The agent quotes these same
          numbers in prose; re-deriving them here is how the two disagree. */}
      <div data-testid="series-stats" className="grid grid-cols-3 gap-2 px-4 pt-3">
        {(
          [
            ["Average", stats?.avg],
            ["Low", stats?.min],
            ["High", stats?.max],
          ] as const
        ).map(([label, v]) => (
          <div key={label} className="rounded-lg border border-border bg-muted/20 px-3 py-2">
            <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
            <div
              className={`mt-0.5 text-sm font-semibold tabular-nums ${
                typeof v === "number" && v < 0 ? "text-rose-600 dark:text-rose-400" : "text-foreground"
              }`}
            >
              {fmtValue(v, unit, yFormat)}
            </div>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div className="relative px-4 py-3">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full touch-none select-none"
          role="img"
          aria-label={`Line chart of ${series.map((s) => s.label).join(", ")}${range ? ` from ${range}` : ""}`}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            if (!rect.width || xs.length < 2) return;
            const t = (((e.clientX - rect.left) / rect.width) * W - M.left) / PLOT_W;
            if (t < -0.02 || t > 1.02) {
              setHover(null);
              return;
            }
            setHover(Math.min(xs.length - 1, Math.max(0, Math.round(t * (xs.length - 1)))));
          }}
          onPointerLeave={() => setHover(null)}
        >
          {/* Recessive y grid + ticks */}
          {chart.yTicks.map((t) => (
            <g key={t}>
              <line
                x1={M.left}
                x2={W - M.right}
                y1={chart.py(t)}
                y2={chart.py(t)}
                className="stroke-border"
                strokeWidth={1}
                opacity={0.5}
              />
              <text
                x={M.left - 8}
                y={chart.py(t)}
                textAnchor="end"
                dominantBaseline="middle"
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {fmtTick(t, yFormat)}
              </text>
            </g>
          ))}

          {/* Zero line — emphasised over the grid: below it is a NEGATIVE price,
              which is a real market state an analyst is looking for. */}
          {/* The y axis always carries a "0" tick when zero is in range (the tick
              step divides 0), so this line needs no label of its own — one was
              tried and collided with the line's right end. */}
          {chart.zeroInView && chart.zeroY != null && (
            <line
              data-testid="zero-line"
              x1={M.left}
              x2={W - M.right}
              y1={chart.zeroY}
              y2={chart.zeroY}
              className="stroke-foreground"
              strokeWidth={1.5}
              opacity={0.55}
            />
          )}

          {/* x ticks — first / middle / last */}
          {[0, Math.floor((xs.length - 1) / 2), xs.length - 1]
            .filter((i, k, a) => i >= 0 && a.indexOf(i) === k)
            .map((i) => (
              <text
                key={i}
                x={Math.min(W - M.right, Math.max(M.left, chart.px(xs[i])))}
                y={H - 8}
                textAnchor={i === 0 ? "start" : i === xs.length - 1 ? "end" : "middle"}
                className="fill-muted-foreground text-[10px] tabular-nums"
              >
                {fmtX(xs[i], xDecl?.type, chart.xSpan)}
              </text>
            ))}

          {/* Uncertainty bands — BEHIND the lines, in the owning series' colour
              at low opacity so the base case stays the figure and the band the
              ground. No stroke: an outlined band reads as two more series. */}
          {chart.areas
            .filter((a) => a.d)
            .map((a) => (
              <path key={`band-${a.key}`} data-testid={`series-band-${a.key}`} d={a.d} fill={a.color} opacity={0.16} />
            ))}

          {/* Reference line — the level a ratio is measured AGAINST (1.0 = the
              baseload average). Dashed so it never reads as data. */}
          {refValue != null && refValue >= chart.lo && refValue <= chart.hi && (
            <g data-testid="series-reference">
              <line
                x1={M.left}
                x2={W - M.right}
                y1={chart.py(refValue)}
                y2={chart.py(refValue)}
                className="stroke-foreground"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                opacity={0.45}
              />
              {refLabel && (
                <text
                  x={W - M.right}
                  y={chart.py(refValue) - 4}
                  textAnchor="end"
                  className="fill-muted-foreground text-[10px]"
                >
                  {refLabel}
                </text>
              )}
            </g>
          )}

          {/* Series — 2px lines, thin marks */}
          {chart.paths.map((s) => (
            <path
              key={s.key}
              data-testid={`series-path-${s.key}`}
              d={s.d}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}

          {/* Hover crosshair */}
          {hoverRow != null && (
            <line
              x1={chart.px(xs[hoverRow])}
              x2={chart.px(xs[hoverRow])}
              y1={M.top}
              y2={M.top + PLOT_H}
              className="stroke-foreground"
              strokeWidth={1}
              opacity={0.35}
            />
          )}
          {hoverRow != null &&
            chart.paths.map((s) => {
              const v = s.values[hoverRow];
              return v == null ? null : (
                <circle
                  key={s.key}
                  cx={chart.px(xs[hoverRow])}
                  cy={chart.py(v)}
                  r={3.5}
                  fill={s.color}
                  className="stroke-background"
                  strokeWidth={2}
                />
              );
            })}
        </svg>

        {hoverRow != null && (
          <div
            data-testid="series-tooltip"
            className="pointer-events-none absolute top-4 rounded-md border border-border bg-background/95 px-2 py-1 text-xs shadow-sm backdrop-blur"
            style={{
              left: `${(chart.px(xs[hoverRow]) / W) * 100}%`,
              transform: chart.px(xs[hoverRow]) > W / 2 ? "translateX(-105%)" : "translateX(5%)",
            }}
          >
            <div className="font-medium text-foreground">{fmtX(xs[hoverRow], xDecl?.type, 0)}</div>
            {chart.paths.map((s) => (
              <div key={s.key} className="flex items-center gap-1.5 text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.color }} aria-hidden="true" />
                <span className="tabular-nums text-foreground">{fmtValue(s.values[hoverRow], s.unit, yFormat)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Legend — identity is never colour-alone. A single series is named by
          the title, so it needs no legend box. */}
      {series.length > 1 && (
        <div data-testid="series-legend" className="flex flex-wrap gap-3 px-4 pb-1">
          {series.map((s) => (
            <span key={s.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-2 w-2 rounded-full" style={{ background: s.color }} aria-hidden="true" />
              {s.label}
              {s.unit ? ` (${s.unit})` : ""}
            </span>
          ))}
        </div>
      )}

      {/* What the chart deliberately left out. A measure on a wildly different
          scale (a 0.73 capture rate beside an 80 EUR/MWh price) cannot share the
          axis without one of them becoming a flat line on the floor — but
          dropping it without saying so is exactly the silent failure #8 forbids.
          It stays in the table and the CSV below. */}
      {deferred.length > 0 && (
        <p data-testid="series-deferred-notice" className="px-4 pb-1 pt-2 text-xs text-muted-foreground">
          Not charted (different scale):{" "}
          <span className="font-medium text-foreground">
            {deferred.map((d) => d.label || d.key).join(", ")}
          </span>
          . {deferred.length === 1 ? "Its value is" : "Their values are"} in the table below.
        </p>
      )}

      {/* Table + CSV — beneath the chart. Columns are the DECLARED axes (never
          a hardcoded column name), sortable by click, rendered through a
          fixed-height window so the DOM stays small even at the 1000-row cap. */}
      <div className="flex flex-col gap-2 px-4 pb-3 pt-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Rows</h3>
          <button
            type="button"
            data-testid="series-csv-download"
            onClick={handleDownloadCsv}
            className="rounded-md border border-border bg-muted/20 px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted/40"
          >
            Export CSV
          </button>
        </div>

        {capped && (
          <p data-testid="series-row-count-notice" className="text-xs font-medium text-amber-600 dark:text-amber-400">
            Showing first {rows.length.toLocaleString("en-US")} of {rowCount.toLocaleString("en-US")} rows.
          </p>
        )}

        <div
          data-testid="series-table-scroll"
          ref={scrollRef}
          onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          className="max-h-80 overflow-auto rounded-lg border border-border"
        >
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-background/95 backdrop-blur">
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    data-testid={`series-col-${c.key}`}
                    scope="col"
                    aria-sort={sortKey === c.key ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
                    className="cursor-pointer select-none whitespace-nowrap border-b border-border px-3 py-2 text-left font-medium text-muted-foreground hover:text-foreground"
                    onClick={() => toggleSort(c.key)}
                  >
                    {c.label}
                    {c.unit ? ` (${c.unit})` : ""}
                    {sortKey === c.key && (
                      <span aria-hidden="true" className="ml-1">
                        {sortDir === "asc" ? "▲" : "▼"}
                      </span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {topPad > 0 && (
                <tr aria-hidden="true" style={{ height: topPad }}>
                  <td colSpan={columns.length} />
                </tr>
              )}
              {windowRows.map((r, i) => (
                <tr key={startIndex + i} data-testid="series-row" className="odd:bg-muted/10">
                  {columns.map((c) => (
                    <td key={c.key} className="whitespace-nowrap border-b border-border/60 px-3 py-1.5 tabular-nums text-foreground">
                      {fmtCell(r[c.key], c)}
                    </td>
                  ))}
                </tr>
              ))}
              {bottomPad > 0 && (
                <tr aria-hidden="true" style={{ height: bottomPad }}>
                  <td colSpan={columns.length} />
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {citation}
    </div>
  );
}
