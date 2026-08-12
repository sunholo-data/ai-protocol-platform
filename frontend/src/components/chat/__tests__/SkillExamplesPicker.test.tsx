import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SkillExamplesPicker } from "../SkillExamplesPicker";
import type { ExampleDocument } from "@/types/skill";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
const mockFetch = vi.mocked(fetchWithAuth);

beforeEach(() => {
  mockFetch.mockReset();
  // Default: preview endpoint fails → cards fall back to the doc icon.
  mockFetch.mockResolvedValue({ ok: false, status: 403 } as Response);
  // jsdom lacks object-URL helpers used by the PDF preview.
  if (!("createObjectURL" in URL)) {
    // @ts-expect-error test shim
    URL.createObjectURL = vi.fn(() => "blob:mock");
    // @ts-expect-error test shim
    URL.revokeObjectURL = vi.fn();
  } else {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  }
});

function makeExample(overrides: Partial<ExampleDocument> = {}): ExampleDocument {
  return {
    bucket: "examples-bucket",
    object: "ppa/contract-a.pdf",
    label: "Example PPA — Fixed price",
    summary: "10-year fixed-price, PaP, German offtaker",
    ...overrides,
  };
}

describe("SkillExamplesPicker", () => {
  it("renders nothing when examples list is empty", () => {
    const { container } = render(
      <SkillExamplesPicker examples={[]} onPickExample={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders one card per example with label + summary", () => {
    const examples = [
      makeExample({ object: "a.pdf", label: "PPA A", summary: "Summary A" }),
      makeExample({ object: "b.pdf", label: "PPA B", summary: "Summary B" }),
    ];
    render(<SkillExamplesPicker examples={examples} onPickExample={() => {}} />);
    expect(screen.getByText("PPA A")).toBeInTheDocument();
    expect(screen.getByText("PPA B")).toBeInTheDocument();
    expect(screen.getByText("Summary A")).toBeInTheDocument();
    expect(screen.getByText("Summary B")).toBeInTheDocument();
  });

  it("renders a clean first-page image (auth-gated thumbnail) for PDF examples", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }),
    } as unknown as Response);
    render(<SkillExamplesPicker examples={[makeExample({ object: "ppa/a.pdf", label: "PPA A" })]} onPickExample={() => {}} />);
    // Rendered via the AUTHENTICATED thumbnail route — never a public URL.
    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/proxy/api/buckets/examples-bucket/thumbnail?object=ppa%2Fa.pdf"),
      ),
    );
    const img = await screen.findByAltText(/first page of PPA A/i);
    expect(img.getAttribute("src")).toContain("blob:mock");
    // It's a plain <img> (no PDF-viewer chrome), not an iframe.
    expect(img.tagName).toBe("IMG");
  });

  it("falls back to the doc icon when the thumbnail is forbidden / unavailable", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<SkillExamplesPicker examples={[makeExample({ object: "ppa/secret.pdf" })]} onPickExample={() => {}} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(screen.queryByAltText(/first page of/i)).toBeNull();
  });

  it("does not fetch a thumbnail for non-PDF examples", () => {
    render(<SkillExamplesPicker examples={[makeExample({ object: "notes/summary.txt" })]} onPickExample={() => {}} />);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("panel layout (default) fills its container height", () => {
    const { container } = render(<SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} />);
    // Bounded Workbench tab: the picker stretches to fill.
    expect(container.firstElementChild?.className).toContain("h-full");
  });

  it("canvas layout does NOT force full height (so following content isn't pushed off-screen)", () => {
    const { container } = render(
      <SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} layout="canvas" />,
    );
    // Wide doc-compare surface: natural height + capped card width.
    expect(container.firstElementChild?.className).not.toContain("h-full");
    expect(container.querySelector("ul")?.className).toContain("auto-fill");
  });

  it("calls onPickExample with the correct example on click", () => {
    const onPickExample = vi.fn();
    const example = makeExample({ label: "Clickable example" });
    render(
      <SkillExamplesPicker examples={[example]} onPickExample={onPickExample} />,
    );
    fireEvent.click(screen.getByText("Clickable example"));
    expect(onPickExample).toHaveBeenCalledWith(example);
  });

  it("falls back to generic doc icon when example.thumbnail is unset", () => {
    const { container } = render(
      <SkillExamplesPicker
        examples={[makeExample({ thumbnail: undefined })]}
        onPickExample={() => {}}
      />,
    );
    // No img element when thumbnail is unset — dashed-border fallback container.
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg[aria-hidden]")).toBeTruthy();
  });

  it("renders an <img> thumbnail when example.thumbnail is set", () => {
    const { container } = render(
      <SkillExamplesPicker
        examples={[makeExample({ thumbnail: "/img/example.png" })]}
        onPickExample={() => {}}
      />,
    );
    // alt="" makes the img decorative — querySelector finds it directly.
    const img = container.querySelector("img");
    expect(img?.src).toContain("/img/example.png");
  });

  it("renders the 'Or upload your own' link only when onUploadOwn is provided", () => {
    const onUploadOwn = vi.fn();
    const { rerender } = render(
      <SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} />,
    );
    expect(
      screen.queryByText(/Or upload your own/i),
    ).not.toBeInTheDocument();

    rerender(
      <SkillExamplesPicker
        examples={[makeExample()]}
        onPickExample={() => {}}
        onUploadOwn={onUploadOwn}
      />,
    );
    fireEvent.click(screen.getByText(/Or upload your own/i));
    expect(onUploadOwn).toHaveBeenCalledOnce();
  });

  it("omits summary in card when not provided", () => {
    render(
      <SkillExamplesPicker
        examples={[makeExample({ label: "Bare PPA", summary: undefined })]}
        onPickExample={() => {}}
      />,
    );
    expect(screen.getByText("Bare PPA")).toBeInTheDocument();
    // No summary text — just confirm only the label exists.
    expect(screen.queryByText(/10-year fixed/)).not.toBeInTheDocument();
  });
});

// v6.12.0 — first-look ACTION cards (welcome.examplePrompts). These advertise
// the skill's real range beyond "import a document"; a click sends the prompt as
// a normal chat message.
describe("SkillExamplesPicker — action prompts", () => {
  const PROMPTS = [
    {
      label: "Chart DK1 market prices",
      badge: "MARKET DATA",
      summary: "Live ENTSO-E day-ahead prices from BigQuery.",
      prompt: "Show me the ENTSO-E day-ahead prices for DK1 for the first week of June 2026.",
    },
    { label: "Compare two PPAs", badge: "COMPARE", prompt: "Compare the Google LEAP PPA with the DemoCorp PPA." },
  ];

  it("renders action cards with badge + summary", () => {
    render(<SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} prompts={PROMPTS} onPickPrompt={() => {}} />);
    expect(screen.getByText("Chart DK1 market prices")).toBeInTheDocument();
    expect(screen.getByText("MARKET DATA")).toBeInTheDocument();
    expect(screen.getByText(/Live ENTSO-E day-ahead prices/)).toBeInTheDocument();
    expect(screen.getByText("Compare two PPAs")).toBeInTheDocument();
  });

  it("clicking a card sends that prompt's text", () => {
    const onPickPrompt = vi.fn();
    render(<SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} prompts={PROMPTS} onPickPrompt={onPickPrompt} />);
    fireEvent.click(screen.getByText("Chart DK1 market prices"));
    expect(onPickPrompt).toHaveBeenCalledWith(PROMPTS[0].prompt);
  });

  it("renders prompts-only (no documents) without an empty doc grid", () => {
    render(<SkillExamplesPicker examples={[]} onPickExample={() => {}} prompts={PROMPTS} onPickPrompt={() => {}} />);
    expect(screen.getByTestId("example-prompts")).toBeInTheDocument();
    // The doc heading must not appear when there are no example documents.
    expect(screen.queryByText(/Pick a document to get started/)).not.toBeInTheDocument();
  });

  it("stays doc-only when no prompts are configured (unchanged behaviour)", () => {
    render(<SkillExamplesPicker examples={[makeExample()]} onPickExample={() => {}} />);
    expect(screen.queryByTestId("example-prompts")).not.toBeInTheDocument();
    expect(screen.getByText(/Pick a document to get started/)).toBeInTheDocument();
  });

  it("renders nothing when there are neither documents nor prompts", () => {
    const { container } = render(<SkillExamplesPicker examples={[]} onPickExample={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
