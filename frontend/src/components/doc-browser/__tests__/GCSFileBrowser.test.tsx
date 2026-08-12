import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { GCSFileBrowser } from "@/components/doc-browser/GCSFileBrowser";

const mockFetch = fetchWithAuth as unknown as ReturnType<typeof vi.fn>;

function file(name: string) {
  return { name, size: 10, contentType: "application/pdf", updated: 0, isPrefix: false };
}
function folder(name: string) {
  return { name, size: 0, isPrefix: true };
}
function page(entries: unknown[], nextPageToken: string | null) {
  return { ok: true, status: 200, json: async () => ({ entries, nextPageToken, prefix: "" }) };
}

describe("GCSFileBrowser pagination", () => {
  beforeEach(() => mockFetch.mockReset());

  it("shows a Load more button when the first page has a nextPageToken", async () => {
    mockFetch.mockResolvedValueOnce(page([file("a.pdf")], "tok-1"));
    render(<GCSFileBrowser bucket="b" onPick={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("a.pdf")).not.toBeNull());
    expect(screen.getByText("Load more")).not.toBeNull();
  });

  it("appends the next page on Load more", async () => {
    mockFetch.mockResolvedValueOnce(page([file("a.pdf")], "tok-1")).mockResolvedValueOnce(page([file("b.pdf")], null));
    render(<GCSFileBrowser bucket="b" onPick={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("a.pdf")).not.toBeNull());
    fireEvent.click(screen.getByText("Load more"));
    await waitFor(() => expect(screen.getByText("b.pdf")).not.toBeNull());
    // first page still present (appended, not replaced)
    expect(screen.getByText("a.pdf")).not.toBeNull();
    // no more pages → button gone
    expect(screen.queryByText("Load more")).toBeNull();
  });

  it("shows no Load more button when there is no nextPageToken", async () => {
    mockFetch.mockResolvedValueOnce(page([file("a.pdf")], null));
    render(<GCSFileBrowser bucket="b" onPick={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("a.pdf")).not.toBeNull());
    expect(screen.queryByText("Load more")).toBeNull();
  });

  it("replaces entries and resets the token when navigating into a folder", async () => {
    mockFetch
      .mockResolvedValueOnce(page([folder("sub/"), file("top.pdf")], "tok-1")) // root, has more
      .mockResolvedValueOnce(page([file("inner.pdf")], null)); // inside sub/, no more
    render(<GCSFileBrowser bucket="b" onPick={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("top.pdf")).not.toBeNull());
    expect(screen.getByText("Load more")).not.toBeNull();

    fireEvent.click(screen.getByText("sub")); // folder button (trailing slash stripped)
    await waitFor(() => expect(screen.getByText("inner.pdf")).not.toBeNull());
    // root entries replaced, not appended
    expect(screen.queryByText("top.pdf")).toBeNull();
    // token reset → no Load more in the new folder
    expect(screen.queryByText("Load more")).toBeNull();
  });
});
