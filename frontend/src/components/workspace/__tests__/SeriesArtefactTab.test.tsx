import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SeriesArtefactTab, rowsToCsv } from "../SeriesArtefactTab";

// Drive the tab off a mocked surface data model — the declared series envelope
// the backend transform (adk/a2ui_entsoe_render.py) stashes at "/".
let dataRoot: unknown = null;
vi.mock("@/providers/SurfaceRegistry", () => ({
  useSurfaceState: () => ({ surface: { dataModel: { get: (p: string) => (p === "/" ? dataRoot : undefined) } } }),
}));

function setData(d: unknown) {
  dataRoot = d;
}

/** The shipped envelope, matching a real DK1 day-ahead result. */
function pricesFixture(overrides: Record<string, unknown> = {}) {
  const rows = [
    { ts: "2026-06-01T00:00:00+00:00", price_eur_mwh: 141.01 },
    { ts: "2026-06-01T01:00:00+00:00", price_eur_mwh: 55.3 },
    { ts: "2026-06-01T02:00:00+00:00", price_eur_mwh: -7.6 },
    { ts: "2026-06-01T03:00:00+00:00", price_eur_mwh: 80.0 },
  ];
  return {
    kind: "series",
    title: "DK1 day-ahead prices",
    x: { key: "ts", label: "Time", type: "time" },
    y: [{ key: "price_eur_mwh", label: "Price", unit: "EUR/MWh" }],
    rows,
    stats: { avg: 67.18, min: -7.6, max: 141.01 },
    sourceUri: "bq://your-project-id.entsoe.day_ahead_prices",
    biddingZone: "DK1",
    startDate: "2026-06-01",
    endDate: "2026-06-07",
    rowCount: rows.length,
    ...overrides,
  };
}

/** Pull the y coordinates out of a path `d` ("M12.00 34.00L…"). */
function pathYs(d: string): number[] {
  return [...d.matchAll(/[ML]\s*[\d.-]+\s+([\d.-]+)/g)].map((m) => Number(m[1]));
}

describe("SeriesArtefactTab (6.12 M2)", () => {
  it("charts a path for the declared y key against the declared x", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    expect(screen.getByText("DK1 day-ahead prices")).toBeInTheDocument();
    const path = screen.getByTestId("series-path-price_eur_mwh");
    // One point per row, in order.
    expect(pathYs(path.getAttribute("d") || "")).toHaveLength(4);
    expect(screen.getByRole("img", { name: /line chart of price/i })).toBeInTheDocument();
  });

  it("reads the axes from the envelope — a different tool's columns chart with no code change", () => {
    // The whole point of the declared envelope: no hardcoded "price_eur_mwh".
    setData({
      kind: "series",
      title: "DK1 generation",
      x: { key: "hour", label: "Hour", type: "time" },
      y: [
        { key: "wind_mw", label: "Wind", unit: "MW" },
        { key: "solar_mw", label: "Solar", unit: "MW" },
      ],
      rows: [
        { hour: "2026-06-01T00:00:00+00:00", wind_mw: 1200, solar_mw: 0 },
        { hour: "2026-06-01T01:00:00+00:00", wind_mw: 900, solar_mw: 340 },
      ],
      stats: { avg: 610, min: 0, max: 1200 },
      startDate: "2026-06-01",
      endDate: "2026-06-01",
    });
    render(<SeriesArtefactTab surfaceId="s" />);

    expect(screen.getByTestId("series-path-wind_mw")).toBeInTheDocument();
    expect(screen.getByTestId("series-path-solar_mw")).toBeInTheDocument();
    // ≥2 series ⇒ a legend is always present (identity never colour-alone).
    const legend = screen.getByTestId("series-legend");
    expect(legend).toHaveTextContent("Wind (MW)");
    expect(legend).toHaveTextContent("Solar (MW)");
  });

  it("plots negative values BELOW the emphasised zero line", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const zero = screen.getByTestId("zero-line");
    const zeroY = Number(zero.getAttribute("y1"));
    expect(Number.isFinite(zeroY)).toBe(true);
    expect(zero.getAttribute("y2")).toBe(zero.getAttribute("y1"));

    const ys = pathYs(screen.getByTestId("series-path-price_eur_mwh").getAttribute("d") || "");
    // Row 2 is −7.6 EUR/MWh. SVG y grows downward ⇒ below zero means y > zeroY.
    expect(ys[2]).toBeGreaterThan(zeroY);
    // The positive rows stay above it.
    expect(ys[0]).toBeLessThan(zeroY);
    expect(ys[1]).toBeLessThan(zeroY);
    expect(ys[3]).toBeLessThan(zeroY);
  });

  it("omits the zero line when the whole series is positive (no dead band)", () => {
    setData(
      pricesFixture({
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", price_eur_mwh: 120 },
          { ts: "2026-06-01T01:00:00+00:00", price_eur_mwh: 140 },
        ],
        stats: { avg: 130, min: 120, max: 140 },
      }),
    );
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.queryByTestId("zero-line")).toBeNull();
  });

  it("shows the SERVER stats verbatim — never a client re-derivation", () => {
    // Deliberately inconsistent with `rows`: the agent quotes `stats` in prose,
    // so the tab must show `stats`, not something it computed itself.
    setData(pricesFixture({ stats: { avg: 55.3, min: -7.6, max: 140.2 } }));
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const stats = screen.getByTestId("series-stats");
    expect(stats).toHaveTextContent("55.30 EUR/MWh");
    expect(stats).toHaveTextContent("-7.60 EUR/MWh");
    expect(stats).toHaveTextContent("140.20 EUR/MWh");
    // 141.01 is the true max of the fixture rows — proof we didn't re-derive.
    expect(stats).not.toHaveTextContent("141.01");
  });

  it("renders an em dash for missing stats rather than a zero", () => {
    setData(pricesFixture({ stats: { avg: null, min: null, max: null } }));
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.getByTestId("series-stats")).toHaveTextContent("—");
  });

  it("cites the bq:// source AND the rendered date range", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const cite = screen.getByTestId("series-citation");
    // The range must be VISIBLE — a wrong range (asked 2026, got 2024) shipped
    // silently to a user; it can only be caught if it's on screen.
    expect(cite).toHaveTextContent("2026-06-01 → 2026-06-07");
    expect(cite).toHaveTextContent("bq://your-project-id.entsoe.day_ahead_prices");
    expect(cite).toHaveTextContent("4 rows");
  });

  it("renders 'no settled prices' — not a blank pane — when every value is null", () => {
    setData(
      pricesFixture({
        rows: [
          { ts: "2026-12-01T00:00:00+00:00", price_eur_mwh: null },
          { ts: "2026-12-01T01:00:00+00:00", price_eur_mwh: null },
        ],
        stats: { avg: null, min: null, max: null },
        startDate: "2026-12-01",
        endDate: "2026-12-07",
      }),
    );
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    expect(screen.getByTestId("series-empty")).toHaveTextContent(/no settled prices in this range/i);
    expect(screen.getByText(/none carry a settled value/i)).toBeInTheDocument();
    expect(screen.queryByTestId("series-path-price_eur_mwh")).toBeNull();
    // The range still shows, so the user can see WHICH range came back unsettled.
    expect(screen.getByTestId("series-citation")).toHaveTextContent("2026-12-01 → 2026-12-07");
  });

  it("renders an explicit notice — not a blank pane — when there are no rows", () => {
    setData(pricesFixture({ rows: [], rowCount: 0, stats: { avg: null, min: null, max: null } }));
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    expect(screen.getByTestId("series-empty")).toHaveTextContent(/no settled prices in this range/i);
    expect(screen.getByText(/returned no rows/i)).toBeInTheDocument();
    expect(screen.getByTestId("series-citation")).toHaveTextContent("2026-06-01 → 2026-06-07");
  });

  it("renders a notice when the surface has no series data at all", () => {
    setData(null);
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.getByTestId("series-empty")).toHaveTextContent(/no data has been returned/i);
  });

  it("breaks the line at nulls instead of bridging an unsettled gap", () => {
    setData(
      pricesFixture({
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", price_eur_mwh: 100 },
          { ts: "2026-06-01T01:00:00+00:00", price_eur_mwh: null },
          { ts: "2026-06-01T02:00:00+00:00", price_eur_mwh: 120 },
        ],
      }),
    );
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const d = screen.getByTestId("series-path-price_eur_mwh").getAttribute("d") || "";
    // Two "M" moves = the pen lifted over the null rather than drawing through it.
    expect(d.match(/M/g)).toHaveLength(2);
    expect(pathYs(d)).toHaveLength(2);
  });

  it("never renders a gs:// path, doc-id or artifact id (CLAUDE.md #9)", () => {
    setData(
      pricesFixture({
        sourceUri: "gs://your-project-id-test-test-llmops-bucket/prices/dk1.parquet",
        docId: "26124699-f558-4f0e-9b1a-1f2b3c4d5e6f",
        artifactId: "entsoe_day_ahead_prices_response_e-1234",
      }),
    );
    const { container } = render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    expect(container.textContent).not.toContain("gs://");
    expect(container.textContent).not.toContain("your-project-id-test-test-llmops-bucket");
    expect(container.textContent).not.toContain("26124699");
    expect(container.textContent).not.toContain("entsoe_day_ahead_prices_response");
    // The chart still renders; only the unvouched source ref is dropped.
    expect(screen.getByTestId("series-path-price_eur_mwh")).toBeInTheDocument();
    expect(screen.getByTestId("series-citation")).toHaveTextContent("2026-06-01 → 2026-06-07");
  });

  it("survives a flat series without collapsing the scale", () => {
    setData(
      pricesFixture({
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", price_eur_mwh: 50 },
          { ts: "2026-06-01T01:00:00+00:00", price_eur_mwh: 50 },
        ],
        stats: { avg: 50, min: 50, max: 50 },
      }),
    );
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const ys = pathYs(screen.getByTestId("series-path-price_eur_mwh").getAttribute("d") || "");
    expect(ys).toHaveLength(2);
    ys.forEach((y) => expect(Number.isFinite(y)).toBe(true));
  });
});

// ── M3 — sortable table + CSV export ────────────────────────────────────────

/** Second `<td>` of each `series-row` — the declared y column under test. */
function rowCells(container: HTMLElement): string[][] {
  return Array.from(container.querySelectorAll('[data-testid="series-row"]')).map((row) =>
    Array.from(row.querySelectorAll("td")).map((td) => td.textContent || ""),
  );
}

describe("SeriesArtefactTab (6.12 M3 — table + CSV)", () => {
  beforeEach(() => {
    // jsdom implements neither of these at runtime — stub so the download
    // handler can run without throwing, and so we can capture what it built.
    global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    global.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("sorts ascending then descending by clicking a column header", () => {
    setData(pricesFixture());
    const { container } = render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    fireEvent.click(screen.getByTestId("series-col-price_eur_mwh"));
    let prices = rowCells(container).map((cells) => cells[1]);
    expect(prices).toEqual(["-7.60 EUR/MWh", "55.30 EUR/MWh", "80.00 EUR/MWh", "141.01 EUR/MWh"]);

    fireEvent.click(screen.getByTestId("series-col-price_eur_mwh"));
    prices = rowCells(container).map((cells) => cells[1]);
    expect(prices).toEqual(["141.01 EUR/MWh", "80.00 EUR/MWh", "55.30 EUR/MWh", "-7.60 EUR/MWh"]);
  });

  it("sorts nulls last in BOTH directions", () => {
    setData(
      pricesFixture({
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", price_eur_mwh: 100 },
          { ts: "2026-06-01T01:00:00+00:00", price_eur_mwh: null },
          { ts: "2026-06-01T02:00:00+00:00", price_eur_mwh: 50 },
          { ts: "2026-06-01T03:00:00+00:00", price_eur_mwh: null },
        ],
      }),
    );
    const { container } = render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    const header = screen.getByTestId("series-col-price_eur_mwh");

    fireEvent.click(header); // ascending
    let prices = rowCells(container).map((cells) => cells[1]);
    expect(prices).toEqual(["50.00 EUR/MWh", "100.00 EUR/MWh", "—", "—"]);

    fireEvent.click(header); // descending
    prices = rowCells(container).map((cells) => cells[1]);
    expect(prices).toEqual(["100.00 EUR/MWh", "50.00 EUR/MWh", "—", "—"]);
  });

  it("follows the declared axes for table columns — the alternate-columns fixture needs no code change", () => {
    setData({
      kind: "series",
      title: "DK1 generation",
      x: { key: "hour", label: "Hour", type: "time" },
      y: [
        { key: "wind_mw", label: "Wind", unit: "MW" },
        { key: "solar_mw", label: "Solar", unit: "MW" },
      ],
      rows: [
        { hour: "2026-06-01T00:00:00+00:00", wind_mw: 1200, solar_mw: 0 },
        { hour: "2026-06-01T01:00:00+00:00", wind_mw: 900, solar_mw: 340 },
      ],
      stats: { avg: 610, min: 0, max: 1200 },
      startDate: "2026-06-01",
      endDate: "2026-06-01",
      rowCount: 2,
    });
    render(<SeriesArtefactTab surfaceId="s" />);

    expect(screen.getByTestId("series-col-hour")).toHaveTextContent("Hour");
    expect(screen.getByTestId("series-col-wind_mw")).toHaveTextContent("Wind (MW)");
    expect(screen.getByTestId("series-col-solar_mw")).toHaveTextContent("Solar (MW)");
    expect(screen.getAllByTestId("series-row")).toHaveLength(2);
  });

  it("CSV escapes commas, quotes, and newlines per RFC 4180, and empties nulls", () => {
    const csv = rowsToCsv(
      [
        { key: "a", label: "A, label" },
        { key: "b", label: "B" },
      ],
      [
        { a: "hello, world", b: "line1\nline2" },
        { a: 'has "quotes"', b: null },
      ],
    );
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe('"A, label",B');
    expect(lines[1]).toBe('"hello, world","line1\nline2"');
    expect(lines[2]).toBe('"has ""quotes""",');
  });

  it("exports the CSV of the CURRENTLY SORTED (visible) rows via the download button", async () => {
    setData(pricesFixture());
    let capturedBlob: Blob | null = null;
    (global.URL.createObjectURL as ReturnType<typeof vi.fn>).mockImplementation((b: Blob) => {
      capturedBlob = b;
      return "blob:mock-url";
    });

    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    fireEvent.click(screen.getByTestId("series-col-price_eur_mwh")); // sort ascending
    fireEvent.click(screen.getByTestId("series-csv-download"));

    expect(capturedBlob).not.toBeNull();
    const text = await (capturedBlob as unknown as Blob).text();

    const fixtureRows = pricesFixture().rows as Record<string, unknown>[];
    // Ascending by price: −7.6 (row 2), 55.3 (row 1), 80 (row 3), 141.01 (row 0).
    const expected = rowsToCsv(
      [
        { key: "ts", label: "Time" },
        { key: "price_eur_mwh", label: "Price" },
      ],
      [fixtureRows[2], fixtureRows[1], fixtureRows[3], fixtureRows[0]],
    );
    expect(text).toBe(expected);
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it('shows "showing first N of M" when the server capped the series — never a silent truncation', () => {
    setData(pricesFixture({ rowCount: 8760 })); // 4 rows shipped, 8760 true total
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);

    const notice = screen.getByTestId("series-row-count-notice");
    expect(notice).toHaveTextContent("Showing first 4 of 8,760 rows.");
  });

  it("shows NO row-count notice when the full series was returned", () => {
    setData(pricesFixture()); // rowCount === rows.length
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.queryByTestId("series-row-count-notice")).toBeNull();
  });
});

// ── ONE-BQ-SHAPES ────────────────────────────────────────────────────────────
// Bands, the one-axis split, and percent formatting. Each fixture mirrors a
// query ONE's analyst keeps in BigQuery Studio; see the backend transform's
// tests for the wire shapes these envelopes are built from.

/** The plot's right edge in viewBox units — chart geometry W(720) - M.right(16). */
const W_RIGHT_EDGE = 704;

/** Her captured-price query: three technologies, each with a low/high band. */
function bandFixture(overrides: Record<string, unknown> = {}) {
  const rows = [
    {
      year: 2027,
      baseload_price_basecase: 100.92,
      baseload_price_lowcase: 88.14,
      baseload_price_highcase: 115.3,
      wind_on_captured_price_basecase: 94.69,
      wind_on_captured_price_lowcase: 82.1,
      wind_on_captured_price_highcase: 108.44,
    },
    {
      year: 2028,
      baseload_price_basecase: 87.28,
      baseload_price_lowcase: 76.02,
      baseload_price_highcase: 99.71,
      wind_on_captured_price_basecase: 80.12,
      wind_on_captured_price_lowcase: 69.44,
      wind_on_captured_price_highcase: 92.03,
    },
  ];
  return {
    kind: "series",
    title: "Captured price by year, Poland",
    x: { key: "year", label: "year", type: "category" },
    y: [
      { key: "baseload_price_basecase", label: "baseload_price" },
      { key: "wind_on_captured_price_basecase", label: "wind_on_captured_price" },
    ],
    bands: [
      {
        key: "baseload_price_basecase",
        lower: "baseload_price_lowcase",
        upper: "baseload_price_highcase",
        label: "baseload_price",
      },
      {
        key: "wind_on_captured_price_basecase",
        lower: "wind_on_captured_price_lowcase",
        upper: "wind_on_captured_price_highcase",
        label: "wind_on_captured_price",
      },
    ],
    deferred: [],
    rows,
    stats: { avg: 94.1, min: 87.28, max: 100.92 },
    rowCount: rows.length,
    ...overrides,
  };
}

describe("SeriesArtefactTab — uncertainty bands", () => {
  beforeEach(() => setData(null));

  it("draws a filled band per declared series", () => {
    setData(bandFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    expect(screen.getByTestId("series-band-baseload_price_basecase")).toBeInTheDocument();
    expect(screen.getByTestId("series-band-wind_on_captured_price_basecase")).toBeInTheDocument();
  });

  it("paints the band in its OWNING series' colour, not a new palette slot", () => {
    setData(bandFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    const line = screen.getByTestId("series-path-wind_on_captured_price_basecase");
    const band = screen.getByTestId("series-band-wind_on_captured_price_basecase");
    expect(band.getAttribute("fill")).toBe(line.getAttribute("stroke"));
  });

  it("includes the band edges in the y domain so the band cannot clip", () => {
    setData(bandFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    // 115.3 (the highcase) is above every plotted LINE value, so if the domain
    // ignored band edges the band's top would sit at or beyond the frame.
    const ys = pathYs(screen.getByTestId("series-band-baseload_price_basecase").getAttribute("d") || "");
    expect(Math.min(...ys)).toBeGreaterThan(0);
  });

  it("keeps the low/high edges as columns in the table and the CSV", () => {
    setData(bandFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    // Collapsing to a band is a CHART decision — the numbers stay reachable.
    expect(screen.getByTestId("series-col-baseload_price_lowcase")).toBeInTheDocument();
    expect(screen.getByTestId("series-col-baseload_price_highcase")).toBeInTheDocument();
  });

  it("renders no band element when the envelope declares none", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.queryByTestId(/^series-band-/)).toBeNull();
  });

  it("breaks the band at a gap rather than bridging missing data", () => {
    setData(
      bandFixture({
        rows: [
          { year: 2027, p_basecase: 100, p_lowcase: 88, p_highcase: 115 },
          { year: 2028, p_basecase: 87, p_lowcase: 76, p_highcase: 99 },
          { year: 2029, p_basecase: 78, p_lowcase: null, p_highcase: 88 },
          { year: 2030, p_basecase: 74, p_lowcase: 66, p_highcase: 83 },
        ],
        y: [{ key: "p_basecase", label: "p" }],
        bands: [{ key: "p_basecase", lower: "p_lowcase", upper: "p_highcase", label: "p" }],
      }),
    );
    render(<SeriesArtefactTab surfaceId="bq" />);
    const d = screen.getByTestId("series-band-p_basecase").getAttribute("d") || "";
    // Exactly ONE closed subpath: 2027-28 fills, the 2029 hole ends it, and the
    // lone 2030 point cannot start a new area. A bridged band would be one
    // subpath spanning all four years — same count, so assert the width too.
    expect(d.match(/M/g)).toHaveLength(1);
    expect(d.endsWith("Z")).toBe(true);
    const xsInPath = [...d.matchAll(/[ML]\s*([\d.-]+)/g)].map((m) => Number(m[1]));
    // The band must stop short of the last x tick (2030), which sits at the
    // plot's right edge.
    expect(Math.max(...xsInPath)).toBeLessThan(W_RIGHT_EDGE);
  });
});

describe("SeriesArtefactTab — one axis, honestly", () => {
  beforeEach(() => setData(null));

  it("names the measures it did not chart, and why", () => {
    setData(
      pricesFixture({
        y: [{ key: "avg_price", label: "avg_price", unit: "EUR/MWh" }],
        deferred: [{ key: "pv_capture_rate", label: "pv_capture_rate" }],
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", avg_price: 87.11, pv_capture_rate: 0.82 },
          { ts: "2026-06-01T01:00:00+00:00", avg_price: 63.02, pv_capture_rate: 0.776 },
        ],
      }),
    );
    render(<SeriesArtefactTab surfaceId="bq" />);
    const notice = screen.getByTestId("series-deferred-notice");
    expect(notice).toHaveTextContent(/not charted \(different scale\)/i);
    expect(notice).toHaveTextContent("pv_capture_rate");
  });

  it("still exposes a deferred measure as a table column", () => {
    setData(
      pricesFixture({
        y: [{ key: "avg_price", label: "avg_price" }],
        deferred: [{ key: "pv_capture_rate", label: "pv_capture_rate" }],
        rows: [
          { ts: "2026-06-01T00:00:00+00:00", avg_price: 87.11, pv_capture_rate: 0.82 },
          { ts: "2026-06-01T01:00:00+00:00", avg_price: 63.02, pv_capture_rate: 0.776 },
        ],
      }),
    );
    render(<SeriesArtefactTab surfaceId="bq" />);
    expect(screen.getByTestId("series-col-pv_capture_rate")).toBeInTheDocument();
  });

  it("shows no notice when everything was charted", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.queryByTestId("series-deferred-notice")).toBeNull();
  });
});

describe("SeriesArtefactTab — ratio charts", () => {
  beforeEach(() => setData(null));

  function ratioFixture() {
    const rows = [
      { month: 1, monthly_price_ratio: 1.184 },
      { month: 6, monthly_price_ratio: 0.792 },
      { month: 12, monthly_price_ratio: 1.093 },
    ];
    return {
      kind: "series",
      title: "Monthly price shape, Poland",
      x: { key: "month", label: "month", type: "category" },
      y: [{ key: "monthly_price_ratio", label: "monthly_price_ratio" }],
      rows,
      stats: { avg: 1.023, min: 0.792, max: 1.184 },
      rowCount: rows.length,
      yFormat: "percent",
      reference: { value: 1.0, label: "Baseload average" },
    };
  }

  it("renders the stat tiles as percentages, not bare ratios", () => {
    setData(ratioFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    expect(screen.getByTestId("series-stats")).toHaveTextContent("102.3%");
  });

  it("draws a labelled reference line at the declared level", () => {
    setData(ratioFixture());
    render(<SeriesArtefactTab surfaceId="bq" />);
    expect(screen.getByTestId("series-reference")).toBeInTheDocument();
    expect(screen.getByText("Baseload average")).toBeInTheDocument();
  });

  it("draws no reference line when the envelope declares none", () => {
    setData(pricesFixture());
    render(<SeriesArtefactTab surfaceId="entsoe_prices" />);
    expect(screen.queryByTestId("series-reference")).toBeNull();
  });
});
