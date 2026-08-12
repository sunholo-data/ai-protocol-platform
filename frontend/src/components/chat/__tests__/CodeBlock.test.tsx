import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ChatMarkdown } from "../ChatMarkdown";
import { hastText, hastLanguage } from "../CodeBlock";

// The ONE Data Analyst shows the SQL behind every number, and the next thing a
// user does with it is run it elsewhere. These tests pin the part that is easy
// to get silently wrong: what actually lands on the clipboard.

const SQL = `SELECT hpfc_year, baseload_price_basecase
FROM \`your-entsoe-project.market_prices.year_captured_prices_poland\`
WHERE hpfc_year > 2026
ORDER BY hpfc_year`;

function renderMarkdown(content: string) {
  return render(<ChatMarkdown content={content} navigateToBlock={() => {}} />);
}

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("CodeBlock — copy affordance", () => {
  it("shows a copy button and the fence language on a fenced block", () => {
    renderMarkdown("```sql\n" + SQL + "\n```");
    expect(screen.getByTestId("code-copy")).toBeInTheDocument();
    expect(screen.getByText("sql")).toBeInTheDocument();
  });

  it("copies the RAW sql, not the syntax-highlighted markup", async () => {
    // The trap: rehypeHighlight has already replaced the text with <span>s by
    // the time `pre` renders, so reading `children` yields "[object Object]".
    renderMarkdown("```sql\n" + SQL + "\n```");
    fireEvent.click(screen.getByTestId("code-copy"));

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("SELECT hpfc_year, baseload_price_basecase");
    expect(copied).toContain("`your-entsoe-project.market_prices.year_captured_prices_poland`");
    expect(copied).not.toContain("<span");
    expect(copied).not.toContain("[object Object]");
  });

  it("preserves line breaks so the pasted query still parses", async () => {
    renderMarkdown("```sql\n" + SQL + "\n```");
    fireEvent.click(screen.getByTestId("code-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect((writeText.mock.calls[0][0] as string).split("\n").length).toBe(4);
  });

  it("confirms the copy to the user", async () => {
    renderMarkdown("```sql\nSELECT 1\n```");
    fireEvent.click(screen.getByTestId("code-copy"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("SHOWS a failure rather than silently doing nothing (CLAUDE.md #8)", async () => {
    // Reachable in practice: navigator.clipboard is undefined on any non-https
    // host, and a button that greys with no feedback is the exact failure #8
    // forbids.
    writeText.mockRejectedValueOnce(new Error("denied"));
    renderMarkdown("```sql\nSELECT 1\n```");
    fireEvent.click(screen.getByTestId("code-copy"));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });

  it("survives clipboard being entirely absent", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    renderMarkdown("```sql\nSELECT 1\n```");
    fireEvent.click(screen.getByTestId("code-copy"));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
  });

  it("gives inline code no copy button", () => {
    renderMarkdown("Use the `base_load` column.");
    expect(screen.queryByTestId("code-copy")).toBeNull();
  });

  it("labels an unfenced block generically rather than showing a blank chip", () => {
    renderMarkdown("```\nplain text\n```");
    expect(screen.getByText("code")).toBeInTheDocument();
    expect(screen.getByTestId("code-copy")).toBeInTheDocument();
  });
});

describe("hast helpers", () => {
  const node = {
    type: "element",
    tagName: "pre",
    children: [
      {
        type: "element",
        tagName: "code",
        properties: { className: ["hljs", "language-sql"] },
        children: [
          { type: "element", tagName: "span", children: [{ type: "text", value: "SELECT " }] },
          { type: "text", value: "1" },
        ],
      },
    ],
  };

  it("walks nested highlight spans to recover the source text", () => {
    expect(hastText(node)).toBe("SELECT 1");
  });

  it("reads the language past rehypeHighlight's added hljs class", () => {
    expect(hastLanguage(node)).toBe("sql");
  });

  it("returns empty rather than throwing on a malformed node", () => {
    expect(hastText(undefined)).toBe("");
    expect(hastText(null)).toBe("");
    expect(hastLanguage({})).toBe("");
  });
});
