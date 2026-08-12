// PPA-COMPARE-LAUNCHER M2 — CompareLauncher tests
//
// The launcher's job: pick exactly two contracts from the open doc tabs +
// the skill's example documents, then fire `start_compare` through the
// surface-action-run loop (opted-in) or compose a chat-intent message
// (fallback). It must NEVER emit a public storage.googleapis.com URL — only
// doc_id / gs:// identities that stay behind the authed backend.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mocks ──────────────────────────────────────────────────────────────────

// Mock the action-driven run hook so we can spy on the POST without a real
// SurfaceRegistry / SSE stream (both covered by their own suites).
interface SpiedAction {
  name: string;
  context: Record<string, unknown>;
}
const triggerActionSpy = vi.fn<[string, SpiedAction], Promise<void>>(async () => {});
vi.mock("@/hooks/useActionDrivenAgent", () => ({
  useActionDrivenAgent: (args: { skillId: string; sessionId: string }) => {
    lastHookArgs = args;
    return { triggerAction: triggerActionSpy };
  },
}));

let lastHookArgs: { skillId: string; sessionId: string } | null = null;

// ─── Imports (after mocks) ──────────────────────────────────────────────────

import { CompareLauncher } from "../CompareLauncher";
import type { DocTabData } from "@/components/doc-browser/DocTab";
import type { ExampleDocument } from "@/types/skill";

// ─── Fixtures ───────────────────────────────────────────────────────────────

function tab(overrides: Partial<DocTabData> = {}): DocTabData {
  return {
    id: "doc-a",
    filename: "Contract A.pdf",
    format: "pdf",
    included: false,
    ...overrides,
  };
}

function example(overrides: Partial<ExampleDocument> = {}): ExampleDocument {
  return {
    bucket: "your-project-id-test-test-llmops-bucket",
    object: "aitana3/PPAs/longform/Example.pdf",
    label: "Example PPA",
    summary: "An example",
    ...overrides,
  };
}

function renderLauncher(props: Partial<React.ComponentProps<typeof CompareLauncher>> = {}) {
  const onCompareViaChat = vi.fn();
  const onConfigure = vi.fn();
  const utils = render(
    <CompareLauncher
      sessionId="sess-1"
      skillId="skill-1"
      optedIn
      docTabs={[]}
      exampleDocuments={[]}
      onCompareViaChat={onCompareViaChat}
      onConfigure={onConfigure}
      {...props}
    />,
  );
  return { ...utils, onCompareViaChat, onConfigure };
}

/** The "Compare contracts" action button (not the heading). */
function compareButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /^compare contracts$/i }) as HTMLButtonElement;
}

beforeEach(() => {
  triggerActionSpy.mockClear();
  lastHookArgs = null;
});

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("CompareLauncher", () => {
  it("renders one selectable row per open doc tab and example document", () => {
    renderLauncher({
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
      exampleDocuments: [example({ object: "PPAs/B.pdf", label: "PPA B" })],
    });
    expect(screen.getByText("A.pdf")).toBeInTheDocument();
    expect(screen.getByText("PPA B")).toBeInTheDocument();
  });

  it("disables the Compare button until exactly two docs are selected", () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
        tab({ id: "doc-c", filename: "C.pdf" }),
      ],
    });
    expect(compareButton()).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    expect(compareButton()).toBeDisabled(); // one selected

    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    expect(compareButton()).toBeEnabled(); // exactly two

    // Deselecting drops back below two → disabled again.
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    expect(compareButton()).toBeDisabled();
  });

  it("blocks selecting a third doc while two are already selected", () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
        tab({ id: "doc-c", filename: "C.pdf" }),
      ],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));

    const third = screen.getByRole("checkbox", { name: /C\.pdf/ });
    expect(third).toBeDisabled();
    fireEvent.click(third);
    expect(third).not.toBeChecked();
    expect(compareButton()).toBeEnabled(); // still the original two
  });

  it("seeds the selection from the doc-tabs bar (included tabs pre-checked)", () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf", included: true }),
        tab({ id: "doc-b", filename: "B.pdf", included: true }),
        tab({ id: "doc-c", filename: "C.pdf", included: false }),
      ],
    });
    // Two included tabs → seeded selection → Compare enabled immediately.
    expect(screen.getByRole("checkbox", { name: /A\.pdf/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /B\.pdf/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /C\.pdf/ })).not.toBeChecked();
    expect(compareButton()).toBeEnabled();
  });

  it("fires start_compare through surface-action-run with both identities and NO chat message", async () => {
    const { onCompareViaChat } = renderLauncher({
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
      exampleDocuments: [example({ object: "aitana3/PPAs/longform/B.pdf", label: "PPA B" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /PPA B/ }));
    fireEvent.click(compareButton());

    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    const [surfaceId, action] = triggerActionSpy.mock.calls[0];
    expect(surfaceId).toBe("workspace");
    expect(action.name).toBe("start_compare");
    expect(action.context.left).toEqual({ doc_id: "doc-a" });
    expect(action.context.right).toEqual({
      gs_url: "gs://your-project-id-test-test-llmops-bucket/aitana3/PPAs/longform/B.pdf",
    });
    expect(action.context.config).toEqual({});
    // No chat message on the opted-in path.
    expect(onCompareViaChat).not.toHaveBeenCalled();
    // Hook scoped to the right skill + session.
    expect(lastHookArgs).toEqual({ skillId: "skill-1", sessionId: "sess-1" });
  });

  it("supports doc_id | gs_url duality (both docs from open tabs → both doc_id)", async () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    fireEvent.click(compareButton());
    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    const action = triggerActionSpy.mock.calls[0][1];
    expect(action.context.left).toEqual({ doc_id: "doc-a" });
    expect(action.context.right).toEqual({ doc_id: "doc-b" });
  });

  it("falls back to a chat-intent message when the skill is NOT opted in", async () => {
    const { onCompareViaChat } = renderLauncher({
      optedIn: false,
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    fireEvent.click(compareButton());

    expect(triggerActionSpy).not.toHaveBeenCalled();
    expect(onCompareViaChat).toHaveBeenCalledTimes(1);
    const msg = onCompareViaChat.mock.calls[0][0] as string;
    expect(msg.toLowerCase()).toContain("compare");
    expect(msg).toContain("A.pdf");
    expect(msg).toContain("B.pdf");
  });

  it("invokes onConfigure when the Configure affordance is clicked", () => {
    const { onConfigure } = renderLauncher({
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    fireEvent.click(screen.getByRole("button", { name: /configure/i }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
  });

  it("never emits a public storage.googleapis.com URL in the action payload", async () => {
    renderLauncher({
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
      exampleDocuments: [example({ object: "aitana3/PPAs/longform/B.pdf", label: "PPA B" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /PPA B/ }));
    fireEvent.click(compareButton());
    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    const serialized = JSON.stringify(triggerActionSpy.mock.calls[0][1]);
    expect(serialized).not.toContain("storage.googleapis.com");
    expect(serialized).not.toContain("https://");
  });

  it("shows guidance and no enabled Compare button when there are no candidates", () => {
    renderLauncher({ docTabs: [], exampleDocuments: [] });
    expect(compareButton()).toBeDisabled();
  });

  // ── M3: inline config form ────────────────────────────────────────────────

  it("toggles the inline config form when Configure is clicked", () => {
    renderLauncher({ docTabs: [tab({ id: "doc-a", filename: "A.pdf" })] });
    expect(screen.queryByTestId("compare-config-form")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /configure/i }));
    expect(screen.getByTestId("compare-config-form")).toBeInTheDocument();
    // Toggle again to hide.
    fireEvent.click(screen.getByRole("button", { name: /configure/i }));
    expect(screen.queryByTestId("compare-config-form")).not.toBeInTheDocument();
  });

  it("threads an applied config subset into the start_compare payload", async () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    // Open the form, narrow to a single clause, apply.
    fireEvent.click(screen.getByRole("button", { name: /configure/i }));
    fireEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /settlement type/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply scope/i }));
    // Form closes after apply.
    expect(screen.queryByTestId("compare-config-form")).not.toBeInTheDocument();

    // Now select the two docs and fire.
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    fireEvent.click(compareButton());

    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    const action = triggerActionSpy.mock.calls[0][1];
    expect(action.context.config).toEqual({ clauses: ["settlement_type"] });
  });

  it("keeps config {} (legacy cache path) when Configure is opened but left at defaults", async () => {
    renderLauncher({
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: /configure/i }));
    fireEvent.click(screen.getByRole("button", { name: /apply scope/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    fireEvent.click(compareButton());
    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    expect(triggerActionSpy.mock.calls[0][1].context.config).toEqual({});
  });

  // ── 7.6 M3: "Analyze obligations" affordance ──────────────────────────────

  /** The "Analyze obligations" action button. */
  function analyzeButton(): HTMLButtonElement {
    return screen.getByRole("button", {
      name: /^analyze obligations$/i,
    }) as HTMLButtonElement;
  }

  it("shows no Analyze button by default (compare-only card unchanged)", () => {
    renderLauncher({ docTabs: [tab({ id: "doc-a", filename: "A.pdf" })] });
    expect(
      screen.queryByRole("button", { name: /^analyze obligations$/i }),
    ).not.toBeInTheDocument();
  });

  it("NEVER SILENT (#8): shows a working indicator while the analyze run is in flight", async () => {
    // Hold the run open so we can observe the in-flight UI.
    let resolveRun: () => void = () => {};
    triggerActionSpy.mockImplementationOnce(
      () => new Promise<void>((res) => (resolveRun = res)),
    );
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(analyzeButton());
    // Button label switches to a working state + a "Working…" status line
    // tells the user where to watch — never just a silent grey button.
    expect(await screen.findByText(/Analyzing…/)).toBeInTheDocument();
    expect(screen.getByText(/Working…/)).toBeInTheDocument();
    resolveRun();
    await waitFor(() => expect(screen.queryByText(/Working…/)).not.toBeInTheDocument());
  });

  it("NEVER SILENT (#8): surfaces a visible error when the run fails, not a dead button", async () => {
    triggerActionSpy.mockRejectedValueOnce(new Error("Agent run failed: model unavailable"));
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(analyzeButton());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/model unavailable/);
  });

  it("opens the picked example in the Document tab on analyze (parity with the bucket picker)", async () => {
    const onOpenDocument = vi.fn();
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      exampleDocuments: [example({ object: "aitana3/PPAs/longform/Google LEAP.pdf", label: "Google LEAP" })],
      onOpenDocument,
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /Google LEAP/ }));
    fireEvent.click(analyzeButton());
    // The launcher opens the document (its gs_url identity) AND runs the analysis.
    await waitFor(() => expect(onOpenDocument).toHaveBeenCalledTimes(1));
    expect(onOpenDocument).toHaveBeenCalledWith({
      gs_url: "gs://your-project-id-test-test-llmops-bucket/aitana3/PPAs/longform/Google LEAP.pdf",
    });
    expect(triggerActionSpy).toHaveBeenCalledTimes(1);
  });

  it("opens the doc even on the chat-fallback (not opted in) analyze path", () => {
    const onOpenDocument = vi.fn();
    const onAnalyzeViaChat = vi.fn();
    renderLauncher({
      optedIn: false,
      allowCompare: false,
      allowObligations: true,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
      onAnalyzeViaChat,
      onOpenDocument,
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(analyzeButton());
    expect(onOpenDocument).toHaveBeenCalledWith({ doc_id: "doc-a" });
    expect(onAnalyzeViaChat).toHaveBeenCalledTimes(1);
  });

  it("obligations-only skill hides Compare + Configure, shows only Analyze", () => {
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    expect(
      screen.queryByRole("button", { name: /^compare contracts$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /configure/i }),
    ).not.toBeInTheDocument();
    expect(analyzeButton()).toBeInTheDocument();
  });

  it("single-select (obligations-only): a second pick REPLACES the first — Analyze stays enabled at exactly one", () => {
    // Obligation analysis is single-doc, so an obligations-only skill is a
    // radio picker (maxSelected 1): picking a second contract swaps out the
    // first rather than dead-ending at "2/1" with the button greyed. Regression
    // for the "pick 1/2, then 2 greys the button" confusion.
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    expect(analyzeButton()).toBeDisabled(); // zero

    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    expect(analyzeButton()).toBeEnabled(); // one (A)
    expect(screen.getByRole("checkbox", { name: /A\.pdf/ })).toBeChecked();

    // Picking B replaces A (radio) — still exactly one, Analyze stays enabled.
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    expect(analyzeButton()).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /B\.pdf/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /A\.pdf/ })).not.toBeChecked();

    // Deselecting the only pick disables Analyze again.
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    expect(analyzeButton()).toBeDisabled(); // zero
  });

  it("fires start_obligation_analysis with {doc} identity and NO chat message", async () => {
    const { onCompareViaChat } = renderLauncher({
      allowCompare: false,
      allowObligations: true,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(analyzeButton());

    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    const [surfaceId, action] = triggerActionSpy.mock.calls[0];
    expect(surfaceId).toBe("workspace");
    expect(action.name).toBe("start_obligation_analysis");
    expect(action.context.doc).toEqual({ doc_id: "doc-a" });
    // No effective_date on the one-click path (mapper asks via chat).
    expect(action.context.effective_date).toBeUndefined();
    expect(onCompareViaChat).not.toHaveBeenCalled();
  });

  it("carries a gs_url identity for an example document", async () => {
    renderLauncher({
      allowCompare: false,
      allowObligations: true,
      exampleDocuments: [example({ object: "aitana3/PPAs/longform/B.pdf", label: "PPA B" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /PPA B/ }));
    fireEvent.click(analyzeButton());
    await waitFor(() => expect(triggerActionSpy).toHaveBeenCalledTimes(1));
    expect(triggerActionSpy.mock.calls[0][1].context.doc).toEqual({
      gs_url: "gs://your-project-id-test-test-llmops-bucket/aitana3/PPAs/longform/B.pdf",
    });
  });

  it("falls back to an obligations chat intent when NOT opted in", () => {
    const onAnalyzeViaChat = vi.fn();
    renderLauncher({
      optedIn: false,
      allowCompare: false,
      allowObligations: true,
      onAnalyzeViaChat,
      docTabs: [tab({ id: "doc-a", filename: "A.pdf" })],
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    fireEvent.click(analyzeButton());

    expect(triggerActionSpy).not.toHaveBeenCalled();
    expect(onAnalyzeViaChat).toHaveBeenCalledTimes(1);
    const msg = onAnalyzeViaChat.mock.calls[0][0] as string;
    expect(msg.toLowerCase()).toContain("obligations");
    expect(msg).toContain("A.pdf");
  });

  it("shows BOTH affordances when the skill supports compare and obligations", () => {
    renderLauncher({
      allowCompare: true,
      allowObligations: true,
      docTabs: [
        tab({ id: "doc-a", filename: "A.pdf" }),
        tab({ id: "doc-b", filename: "B.pdf" }),
      ],
    });
    expect(compareButton()).toBeInTheDocument();
    expect(analyzeButton()).toBeInTheDocument();
    // One selected → Analyze enabled, Compare disabled.
    fireEvent.click(screen.getByRole("checkbox", { name: /A\.pdf/ }));
    expect(analyzeButton()).toBeEnabled();
    expect(compareButton()).toBeDisabled();
    // Two selected → Compare enabled, Analyze disabled.
    fireEvent.click(screen.getByRole("checkbox", { name: /B\.pdf/ }));
    expect(compareButton()).toBeEnabled();
    expect(analyzeButton()).toBeDisabled();
  });
});
