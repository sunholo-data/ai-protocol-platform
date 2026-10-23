/**
 * Created folder + generated-document badge — v6.32.0 document-creation M3.
 *
 * Two things are pinned here:
 *
 *  1. The **badge**: a user must be able to tell what the assistant made from
 *     what they uploaded.
 *  2. The **auto-select guard**: folders sort newest-first, so a plain
 *     `loaded[0]` would make the system "Created" folder the default view the
 *     moment the assistant generates a document — showing the user the
 *     assistant's output instead of their own documents.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocListFolder } from "../DocListFolder";
import { DocListItem } from "../DocListItem";
import type { Folder, ParsedDocument } from "@/hooks/useDocBrowser";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("@/components/document/DocumentPreview", () => ({
  DocumentPreview: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function folder(over: Partial<Folder> = {}): Folder {
  return {
    id: "f1",
    name: "Uploads",
    userId: "u1",
    docCount: 2,
    parsedCount: 2,
    ...over,
  };
}

function doc(over: Partial<ParsedDocument> = {}): ParsedDocument {
  return {
    id: "d1",
    originalFilename: "report.docx",
    sourceFormat: "docx",
    parseStatus: "parsed",
    parseError: null,
    folderId: "f1",
    userId: "u1",
    blockCount: 5,
    hasA2ui: false,
    ...over,
  };
}

describe("Created badge", () => {
  it("badges a system folder", () => {
    render(
      <DocListFolder
        folder={folder({ name: "Created", system: true })}
        documents={[]}
        isActive={false}
        onSelect={vi.fn()}
        onDocClick={vi.fn()}
      />,
    );
    expect(screen.getByTitle("Documents the assistant created for you")).toBeInTheDocument();
  });

  it("does not badge a user's own folder", () => {
    render(
      <DocListFolder
        folder={folder()}
        documents={[]}
        isActive={false}
        onSelect={vi.fn()}
        onDocClick={vi.fn()}
      />,
    );
    expect(screen.queryByTitle("Documents the assistant created for you")).not.toBeInTheDocument();
  });

  it("badges a generated document", () => {
    render(<DocListItem doc={doc({ source: "generated" })} onClick={vi.fn()} />);
    expect(screen.getByTitle("Created by the assistant")).toBeInTheDocument();
  });

  it("does not badge an uploaded document", () => {
    render(<DocListItem doc={doc({ source: "uploaded" })} onClick={vi.fn()} />);
    expect(screen.queryByTitle("Created by the assistant")).not.toBeInTheDocument();
  });

  it("treats a document with no source field as uploaded", () => {
    // Every record written before v6.32.0 has no `source`. None of them are
    // the assistant's output, so none should be badged.
    render(<DocListItem doc={doc()} onClick={vi.fn()} />);
    expect(screen.queryByTitle("Created by the assistant")).not.toBeInTheDocument();
  });
});

/**
 * The selection rule itself, extracted so it can be asserted without mounting
 * Firestore. This mirrors the expression in `useDocBrowser`; if that changes,
 * this must change with it.
 */
function pickDefaultFolder(loaded: Folder[]): string | null {
  return loaded.find((f) => !f.system)?.id ?? loaded[0]?.id ?? null;
}

describe("folder auto-select", () => {
  it("does not jump to a newly created system folder", () => {
    // Sorted newest-first, as useDocBrowser sorts them: "Created" is newest.
    const loaded = [
      folder({ id: "created-id", name: "Created", system: true, createdAt: "2026-09-01" }),
      folder({ id: "uploads-id", name: "Uploads", createdAt: "2026-01-01" }),
    ];
    expect(pickDefaultFolder(loaded)).toBe("uploads-id");
  });

  it("still selects the newest folder the user owns", () => {
    const loaded = [
      folder({ id: "contracts-id", name: "Contracts", createdAt: "2026-08-01" }),
      folder({ id: "uploads-id", name: "Uploads", createdAt: "2026-01-01" }),
    ];
    expect(pickDefaultFolder(loaded)).toBe("contracts-id");
  });

  it("falls back to a system folder only when there is nothing else", () => {
    const loaded = [folder({ id: "created-id", name: "Created", system: true })];
    expect(pickDefaultFolder(loaded)).toBe("created-id");
  });

  it("returns null when there are no folders at all", () => {
    expect(pickDefaultFolder([])).toBeNull();
  });
});
