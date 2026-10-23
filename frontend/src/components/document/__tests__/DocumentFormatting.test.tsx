// v6.34.0 DOC-WORKBENCH P3 — formatting on the surface.
//
// The properties worth pinning are about what gets SENT and what happens when
// it fails, not the layout:
//   * templates are chosen by FILE NAME, never a doc-id (CLAUDE.md #9);
//   * the knobs that only make sense inside a template are unusable without one
//     (upstream returns REFERENCE_DOC_NOT_APPLICABLE — a control that cannot
//     work should not look available);
//   * the backend's typed errors reach the user verbatim, because
//     TEMPLATE_NOT_FOUND and NO_EDITABLE_SOURCE are both actionable.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentFormatting } from "../DocumentFormatting";
import { fetchWithAuth } from "@/lib/apiClient";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockFetch = vi.mocked(fetchWithAuth);

const TEMPLATES = {
  ok: true,
  status: 200,
  json: async () => ({ templates: [{ filename: "Acme letterhead.docx" }, { filename: "Report.docx" }] }),
} as unknown as Response;

function reply(status: number, body: unknown = {}) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(TEMPLATES);
});

async function renderPanel() {
  render(<DocumentFormatting docId="doc-1" />);
  await screen.findByRole("option", { name: "Acme letterhead.docx" });
}

describe("DocumentFormatting", () => {
  it("offers the user's own .docx documents as templates", async () => {
    await renderPanel();
    expect(screen.getByRole("option", { name: "Report.docx" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Plain/ })).toBeInTheDocument();
  });

  it("sends the template by NAME, never a doc-id", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200));

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Acme letterhead.docx");
    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const [url, init] = mockFetch.mock.calls[1];
    expect(url).toBe("/api/proxy/api/documents/doc-1/revise");
    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent.template).toBe("Acme letterhead.docx");
    expect(sent).not.toHaveProperty("docId");
  });

  it("restyles without new content, so the stored source is reused", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200));

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Report.docx");
    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const sent = JSON.parse((mockFetch.mock.calls[1][1] as RequestInit).body as string);
    expect(sent).not.toHaveProperty("contentMarkdown");
  });

  it("disables the template-only knobs until a template is chosen", async () => {
    await renderPanel();
    expect(screen.getByLabelText("Table look")).toBeDisabled();
    expect(screen.getByLabelText("Page setup section")).toBeDisabled();

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Report.docx");

    expect(screen.getByLabelText("Table look")).toBeEnabled();
    expect(screen.getByLabelText("Page setup section")).toBeEnabled();
  });

  it("sends the native knobs when they are set", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200));

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Report.docx");
    await userEvent.type(screen.getByLabelText("Table look"), "Table Grid");
    await userEvent.type(screen.getByLabelText("Page setup section"), "2");
    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const sent = JSON.parse((mockFetch.mock.calls[1][1] as RequestInit).body as string);
    expect(sent.tableStyle).toBe("Table Grid");
    expect(sent.referenceSection).toBe(2);
  });

  it("omits an empty section rather than sending a NaN", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200));

    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    const sent = JSON.parse((mockFetch.mock.calls[1][1] as RequestInit).body as string);
    expect(sent).not.toHaveProperty("referenceSection");
  });

  it("shows the backend's typed error verbatim", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(
      reply(409, { detail: { code: "NO_EDITABLE_SOURCE", message: "an uploaded file cannot be edited in place." } }),
    );

    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot be edited in place/);
  });

  it("falls back to a status message when there is no typed detail", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(500));

    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("500");
  });

  it("confirms success visibly, saying what was actually done", async () => {
    // Was `expect(findByText("Updated"))`. "Updated" was true of every outcome —
    // template carried, template ignored, no template at all — which is what
    // let a restyle that had worked read as one that had not (2026-09-04).
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200));

    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    expect(await screen.findByRole("status")).toHaveTextContent("Restyled without a template");
  });

  it("uploads a new template through the ordinary upload path and selects it", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(200, { originalFilename: "New letterhead.docx" }));

    const file = new File(["x"], "New letterhead.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    await userEvent.upload(screen.getByLabelText("Upload a template"), file);

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(mockFetch.mock.calls[1][0]).toBe("/api/proxy/api/documents/upload");
    await waitFor(() =>
      expect((screen.getByLabelText("Template") as HTMLSelectElement).value).toBe("New letterhead.docx"),
    );
  });

  it("reports a failed template upload", async () => {
    await renderPanel();
    mockFetch.mockResolvedValue(reply(403));

    const file = new File(["x"], "t.docx");
    await userEvent.upload(screen.getByLabelText("Upload a template"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent("403");
  });

  it("stays usable when the template list fails to load", async () => {
    mockFetch.mockReset();
    mockFetch.mockResolvedValue(reply(500));
    render(<DocumentFormatting docId="doc-1" />);

    // The native controls and Apply are still there; only the list is missing.
    expect(await screen.findByRole("button", { name: /apply formatting/i })).toBeInTheDocument();
  });
});

// --- The panel must describe the DOCUMENT, not its own lifetime -------------
//
// Reported 2026-09-04: "the page refreshes and the template copy-look-from
// returns to Plain (no template), so I'm not sure it actually applied". It had
// applied — the panel simply held no state about what the document was styled
// from, so a successful restyle and a forgotten one looked identical.

describe("DocumentFormatting — what this document is styled from", () => {
  it("opens on the template the document actually uses, not on Plain", async () => {
    render(
      <DocumentFormatting
        docId="doc-1"
        currentTemplate={{ filename: "Acme letterhead.docx", applied: true, partsCarried: 12 }}
      />,
    );
    await screen.findByRole("option", { name: "Acme letterhead.docx" });
    expect(screen.getByLabelText("Template")).toHaveValue("Acme letterhead.docx");
  });

  it("says which template is applied, and how much of it carried", async () => {
    render(
      <DocumentFormatting
        docId="doc-1"
        currentTemplate={{ filename: "Acme letterhead.docx", applied: true, partsCarried: 12 }}
      />,
    );
    expect(await screen.findByTestId("current-template")).toHaveTextContent(
      /Currently styled from.*Acme letterhead\.docx.*12 template parts carried/,
    );
  });

  it("distinguishes 'applied but carried nothing' from 'did not apply'", async () => {
    const { rerender } = render(
      <DocumentFormatting
        docId="doc-1"
        currentTemplate={{ filename: "Empty.docx", applied: true, partsCarried: 0 }}
      />,
    );
    expect(await screen.findByTestId("current-template")).toHaveTextContent(/no styling parts to carry/);

    rerender(
      <DocumentFormatting
        docId="doc-1"
        currentTemplate={{ filename: "Broken.docx", applied: false, partsCarried: 0 }}
      />,
    );
    expect(screen.getByTestId("current-template")).toHaveTextContent(/did not apply it/);
  });

  it("does not claim 'no template' for a document styled before names were recorded", async () => {
    // Every document restyled before this change carries only the opaque
    // templateDocId. It IS styled — saying otherwise is a lie about the file.
    render(
      <DocumentFormatting docId="doc-1" currentTemplate={{ filename: "", applied: true, partsCarried: 0 }} />,
    );
    const line = await screen.findByTestId("current-template");
    expect(line).toHaveTextContent(/name wasn't recorded/i);
    expect(line).not.toHaveTextContent(/Not styled from a template/);
  });

  it("says 'not styled from a template' when nothing was applied", async () => {
    render(<DocumentFormatting docId="doc-1" currentTemplate={null} />);
    expect(await screen.findByTestId("current-template")).toHaveTextContent(/Not styled from a template/);
  });

  it("reports the converter's verdict after Apply, not a bare 'Updated'", async () => {
    render(<DocumentFormatting docId="doc-1" />);
    await screen.findByRole("option", { name: "Acme letterhead.docx" });
    mockFetch.mockResolvedValue(
      reply(200, { templateUsed: "Acme letterhead.docx", referenceDocApplied: true, templatePartsCarried: 12 }),
    );

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Acme letterhead.docx");
    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Applied Acme letterhead\.docx.*12 parts carried/);
  });

  it("does not claim success when the backend says the template did not apply", async () => {
    render(<DocumentFormatting docId="doc-1" />);
    await screen.findByRole("option", { name: "Acme letterhead.docx" });
    mockFetch.mockResolvedValue(
      reply(200, { templateUsed: "Broken.docx", referenceDocApplied: false, templatePartsCarried: 0 }),
    );

    await userEvent.selectOptions(screen.getByLabelText("Template"), "Acme letterhead.docx");
    await userEvent.click(screen.getByRole("button", { name: /apply formatting/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Broken\.docx did not apply/);
  });
});
