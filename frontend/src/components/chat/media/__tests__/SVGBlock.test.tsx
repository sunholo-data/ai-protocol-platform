import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SVGBlock } from "@/components/chat/media/SVGBlock";

// Mock dompurify so tests don't depend on a real DOM for sanitization logic.
// The mock applies the same contracts as the real DOMPurify:
//   - strips <script> tags and onerror attributes
//   - strips <use> elements (external reference vector)
//   - returns empty string for empty input
vi.mock("dompurify", () => ({
  default: {
    sanitize: (input: string, config?: Record<string, unknown>) => {
      const forbidTags = (config?.FORBID_TAGS as string[] | undefined) ?? [];
      let out = input;
      for (const tag of forbidTags) {
        out = out.replace(new RegExp(`<${tag}[^>]*>.*?<\\/${tag}>`, "gis"), "");
        out = out.replace(new RegExp(`<${tag}[^/]*/?>`, "gi"), "");
      }
      // Strip event handler attributes
      out = out.replace(/\s+on\w+="[^"]*"/gi, "");
      return out.trim();
    },
  },
}));

const SIMPLE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <circle cx="50" cy="50" r="40" fill="blue"/>
</svg>`;

const SVG_WITH_SCRIPT = `<svg xmlns="http://www.w3.org/2000/svg">
  <script>alert('xss')</script>
  <circle cx="50" cy="50" r="40"/>
</svg>`;

const SVG_WITH_USE = `<svg xmlns="http://www.w3.org/2000/svg">
  <use href="external.svg#icon"/>
  <circle cx="50" cy="50" r="40"/>
</svg>`;

describe("SVGBlock", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders clean SVG markup inside a container div", async () => {
    const { container } = render(<SVGBlock svgString={SIMPLE_SVG} />);
    await waitFor(() => {
      expect(container.querySelector(".svg-container")).toBeTruthy();
    });
  });

  it("strips <script> tags from SVG before rendering", async () => {
    const { container } = render(<SVGBlock svgString={SVG_WITH_SCRIPT} />);
    await waitFor(() => {
      expect(container.querySelector(".svg-container")).toBeTruthy();
    });
    expect(container.querySelector("script")).toBeFalsy();
    expect(container.innerHTML).not.toContain("alert");
  });

  it("strips <use> tags (external reference vector)", async () => {
    const { container } = render(<SVGBlock svgString={SVG_WITH_USE} />);
    await waitFor(() => {
      expect(container.querySelector(".svg-container")).toBeTruthy();
    });
    expect(container.innerHTML).not.toContain("<use");
  });

  it("shows a placeholder before the async sanitize resolves (no layout-shift gap)", () => {
    const { container } = render(<SVGBlock svgString={SIMPLE_SVG} />);
    // Before the dynamic DOMPurify import + sanitize resolves, cleanSvg is ''
    // so the placeholder renders (reserving space) rather than nothing. The
    // real SVG (.svg-container) only appears after the effect resolves.
    expect(container.querySelector(".svg-placeholder")).toBeTruthy();
    expect(container.querySelector(".svg-container")).toBeFalsy();
  });

  it("offers an explicit Expand affordance (a bare SVG doesn't read as clickable)", async () => {
    render(<SVGBlock svgString={SIMPLE_SVG} />);
    // Two triggers by design: the SVG itself (cursor-zoom-in), and a visible
    // "Expand" button because a bare SVG doesn't read as clickable.
    await screen.findByLabelText("Expand diagram to full screen");
    expect(screen.getAllByRole("button", { name: /expand/i }).length).toBe(2);
  });

  it("scales a fixed-size SVG to the container (this is the 'make it larger' fix)", async () => {
    // The model emits width/height + a viewBox. We drop the fixed dimensions so
    // CSS width:100% governs the size — otherwise it renders at its baked-in px.
    const fixed = `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" viewBox="0 0 300 200"><rect width="300" height="200"/></svg>`;
    const { container } = render(<SVGBlock svgString={fixed} />);
    await waitFor(() => expect(container.querySelector(".svg-container svg")).toBeTruthy());
    const svg = container.querySelector(".svg-container svg")!;
    expect(svg.getAttribute("width")).toBeNull();
    expect(svg.getAttribute("height")).toBeNull();
    expect(svg.getAttribute("viewBox")).toBe("0 0 300 200");
  });

  it("synthesises a viewBox from width/height when the model omitted one", async () => {
    // Without a viewBox an SVG can't scale — so we derive one before dropping
    // the dimensions. (An SVG with neither can't be made responsive safely.)
    const noViewBox = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480"/></svg>`;
    const { container } = render(<SVGBlock svgString={noViewBox} />);
    await waitFor(() => expect(container.querySelector(".svg-container svg")).toBeTruthy());
    const svg = container.querySelector(".svg-container svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 640 480");
    expect(svg.getAttribute("width")).toBeNull();
  });

  it("leaves an SVG with no viewBox and no usable dimensions untouched (width:100% would collapse it)", async () => {
    const bare = `<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>`;
    const { container } = render(<SVGBlock svgString={bare} />);
    await waitFor(() => expect(container.querySelector(".svg-container svg")).toBeTruthy());
    const svg = container.querySelector(".svg-container svg")!;
    // No viewBox to add and nothing to derive it from → we do not force scaling.
    expect(svg.getAttribute("viewBox")).toBeNull();
  });

  it("opens a full-screen dialog with the SAME sanitised markup", async () => {
    render(<SVGBlock svgString={SVG_WITH_SCRIPT} />);
    // Inline trigger is the .svg-container button.
    const trigger = await screen.findByLabelText("Expand diagram to full screen");
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeTruthy();
    // The dialog renders the sanitised string — the script must not reappear
    // just because it's now in a modal (no re-introduction of raw svgString).
    expect(dialog.innerHTML).not.toContain("alert");
    expect(dialog.querySelector("script")).toBeFalsy();
    expect(dialog.querySelector("circle")).toBeTruthy();
  });

  it("gives the full-screen dialog a DEFINITE size so the SVG scales up, not shrink-wraps", async () => {
    // Regression: with only max-w-/max-h- the flex-col content shrink-wraps to
    // the SVG's intrinsic size, so `w-full` on the inner <svg> collapses back to
    // the baked-in size and the "expanded" diagram came out the same or smaller
    // than inline. A definite 92vw×92vh box is what lets the SVG fill and enlarge.
    render(<SVGBlock svgString={SIMPLE_SVG} />);
    fireEvent.click(await screen.findByLabelText("Expand diagram to full screen"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.className).toContain("w-[92vw]");
    expect(dialog.className).toContain("h-[92vh]");
    // ...and the SVG is told to fill that box (both axes; aspect preserved by
    // the SVG's default preserveAspectRatio "meet"). Read via the className
    // PROPERTY, not innerHTML (which HTML-encodes the & to &amp;).
    const panes = Array.from(dialog.querySelectorAll("div")).map((d) => d.className);
    expect(panes.some((c) => c.includes("[&_svg]:h-full") && c.includes("[&_svg]:w-full"))).toBe(true);
  });

  it("can be closed again (does not trap the user full-screen)", async () => {
    render(<SVGBlock svgString={SIMPLE_SVG} />);
    fireEvent.click(await screen.findByLabelText("Expand diagram to full screen"));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByLabelText("Close full-screen diagram"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

// ChatMarkdown SVG integration tests
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

describe("ChatMarkdown SVG rendering", () => {
  const noop = () => {};

  it("renders ```svg fenced block as SVGBlock (svg-container present)", async () => {
    const md = "```svg\n<svg xmlns='http://www.w3.org/2000/svg'><circle r='10'/></svg>\n```";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    await waitFor(() => {
      expect(container.querySelector(".svg-container")).toBeTruthy();
    });
  });

  it("renders ```js fenced block as normal code block (no svg-container)", () => {
    const md = "```js\nconsole.log('hello');\n```";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    expect(container.querySelector(".svg-container")).toBeFalsy();
    expect(container.querySelector("code")).toBeTruthy();
  });
});
