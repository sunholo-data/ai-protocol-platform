import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { useDocBrowser } from "@/hooks/useDocBrowser";
import { DocListView } from "../DocListView";

type State = ReturnType<typeof useDocBrowser>;
let state: State;
vi.mock("@/hooks/useDocBrowser", () => ({ useDocBrowser: () => state }));
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/components/document/DocumentPreview", () => ({
  DocumentPreview: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function base(over: Partial<State> = {}): State {
  return {
    folders: [],
    activeFolderId: null,
    documents: [],
    searchQuery: "",
    filteredDocuments: [],
    setActiveFolderId: vi.fn(),
    setSearchQuery: vi.fn(),
    loading: false,
    error: null,
    retry: vi.fn(),
    ...over,
  };
}

describe("DocListView", () => {
  beforeEach(() => {
    state = base();
  });

  it("shows the empty state when there really are no documents", () => {
    render(<DocListView uid="u1" />);
    expect(screen.getByText(/No documents yet/)).toBeInTheDocument();
  });

  it("shows a loading line, not the empty state, while the API loads", () => {
    state = base({ loading: true });
    render(<DocListView uid="u1" />);
    expect(screen.getByText(/Loading documents/)).toBeInTheDocument();
    expect(screen.queryByText(/No documents yet/)).not.toBeInTheDocument();
  });

  it("renders a load error visibly with a Retry (NEVER SILENT)", async () => {
    const retry = vi.fn();
    state = base({ error: "Couldn't load your folders (HTTP 500).", retry });
    render(<DocListView uid="u1" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load your folders (HTTP 500).");
    expect(screen.queryByText(/No documents yet/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
