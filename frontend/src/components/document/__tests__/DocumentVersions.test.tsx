// v6.34.0 DOC-WORKBENCH P3 — the iterate loop is only real if you can go back.
//
// What matters here is the states that are NOT the happy path: no history is a
// normal condition (an uploaded document has none), and a failure has to say so
// rather than render an empty box (NEVER-SILENT #8).

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentVersions } from "../DocumentVersions";
import { fetchWithAuth } from "@/lib/apiClient";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockFetch = vi.mocked(fetchWithAuth);

function json(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
  global.URL.createObjectURL = vi.fn(() => "blob:mock");
  global.URL.revokeObjectURL = vi.fn();
});

describe("DocumentVersions", () => {
  it("lists versions newest first and marks the current one", async () => {
    mockFetch.mockResolvedValue(
      json({ docId: "d1", filename: "Offer.docx", currentVersion: 2, versions: [0, 1, 2] }),
    );
    render(<DocumentVersions docId="d1" filename="Offer.docx" />);

    expect(await screen.findByText("Version 2")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Version 2");
    expect(items[0]).toHaveTextContent("on screen");
    expect(items[2]).toHaveTextContent("Version 0");
  });

  it("says there is no history rather than rendering nothing", async () => {
    // An uploaded document, or one created before versioning shipped. Normal,
    // not an error — but the user must be told which.
    mockFetch.mockResolvedValue(json({ docId: "d1", filename: "x.docx", currentVersion: null, versions: [] }));
    render(<DocumentVersions docId="d1" filename="x.docx" />);

    expect(await screen.findByText(/hasn't been regenerated yet/i)).toBeInTheDocument();
  });

  it("reports a 403 in words", async () => {
    mockFetch.mockResolvedValue(json({}, 403));
    render(<DocumentVersions docId="d1" filename="x.docx" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/access/i);
  });

  it("reports a server failure with its status", async () => {
    mockFetch.mockResolvedValue(json({}, 500));
    render(<DocumentVersions docId="d1" filename="x.docx" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("500");
  });

  it("reports a network error instead of hanging on the spinner", async () => {
    mockFetch.mockRejectedValue(new Error("offline"));
    render(<DocumentVersions docId="d1" filename="x.docx" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });

  it("downloads a specific version through the authenticated route", async () => {
    mockFetch
      .mockResolvedValueOnce(json({ docId: "d1", filename: "Offer.docx", currentVersion: 1, versions: [0, 1] }))
      .mockResolvedValueOnce({ ok: true, status: 200, blob: async () => new Blob(["v0"]) } as unknown as Response);

    render(<DocumentVersions docId="d1" filename="Offer.docx" />);
    await screen.findByText("Version 0");

    await userEvent.click(screen.getByLabelText("Download version 0"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    expect(mockFetch.mock.calls[1][0]).toBe("/api/proxy/api/documents/d1/download?version=0");
  });

  it("surfaces a failed version download", async () => {
    mockFetch
      .mockResolvedValueOnce(json({ docId: "d1", filename: "Offer.docx", currentVersion: 1, versions: [0, 1] }))
      .mockResolvedValueOnce(json({}, 404));

    render(<DocumentVersions docId="d1" filename="Offer.docx" />);
    await screen.findByText("Version 0");

    await userEvent.click(screen.getByLabelText("Download version 0"));

    expect(await screen.findByRole("alert")).toHaveTextContent("v0");
  });

  it("url-encodes the doc id", async () => {
    mockFetch.mockResolvedValue(json({ docId: "a/b", filename: "x", currentVersion: null, versions: [] }));
    render(<DocumentVersions docId="a/b" filename="x" />);

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/documents/a%2Fb/versions");
  });
});
