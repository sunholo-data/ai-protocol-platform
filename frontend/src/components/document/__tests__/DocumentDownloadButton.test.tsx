// v6.34.0 DOC-WORKBENCH P2 — the download must be authenticated and must fail loudly.
//
// Two properties worth a test rather than an eyeball:
//   * it goes through `fetchWithAuth` and the /api/proxy route, NOT a raw
//     gs:// or storage.googleapis.com URL (CLAUDE.md security rule);
//   * a 403 renders something the user can read (NEVER-SILENT #8) — the button
//     that greys and does nothing is exactly the failure this rule targets.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentDownloadButton } from "../DocumentDownloadButton";
import { fetchWithAuth } from "@/lib/apiClient";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

const mockFetch = vi.mocked(fetchWithAuth);

beforeEach(() => {
  mockFetch.mockReset();
  // jsdom implements neither.
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

function ok(body = "bytes") {
  return { ok: true, status: 200, blob: async () => new Blob([body]) } as unknown as Response;
}

describe("DocumentDownloadButton", () => {
  it("fetches through the authenticated proxy route", async () => {
    mockFetch.mockResolvedValue(ok());
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).toBe("/api/proxy/api/documents/doc-1/download");
  });

  it("never requests a public or signed storage URL", async () => {
    mockFetch.mockResolvedValue(ok());
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const url = mockFetch.mock.calls[0][0] as string;
    expect(url).not.toContain("storage.googleapis.com");
    expect(url).not.toContain("gs://");
  });

  it("url-encodes a doc id so a slash cannot escape the path", async () => {
    mockFetch.mockResolvedValue(ok());
    render(<DocumentDownloadButton docId="a/b" filename="x.docx" />);

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/documents/a%2Fb/download");
  });

  it("saves under the document's own filename", async () => {
    mockFetch.mockResolvedValue(ok());
    const clicked: HTMLAnchorElement[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag) as HTMLAnchorElement;
      if (tag === "a") {
        el.click = () => clicked.push(el);
      }
      return el;
    });

    render(<DocumentDownloadButton docId="doc-1" filename="Offer - Northwind.docx" />);
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0].download).toBe("Offer - Northwind.docx");
    vi.mocked(document.createElement).mockRestore();
  });

  it("shows a readable message on 403 instead of failing silently", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    await userEvent.click(screen.getByRole("button"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/access/i);
  });

  it("surfaces a non-403 failure with its status", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 502 } as Response);
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    await userEvent.click(screen.getByRole("button"));

    expect(await screen.findByRole("alert")).toHaveTextContent("502");
  });

  it("surfaces a network error rather than swallowing it", async () => {
    mockFetch.mockRejectedValue(new Error("offline"));
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    await userEvent.click(screen.getByRole("button"));

    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });

  it("re-enables after a failure so the user can retry", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 502 } as Response);
    render(<DocumentDownloadButton docId="doc-1" filename="Offer.docx" />);

    const button = screen.getByRole("button");
    await userEvent.click(button);

    await screen.findByRole("alert");
    await waitFor(() => expect(button).not.toBeDisabled());
  });
});
