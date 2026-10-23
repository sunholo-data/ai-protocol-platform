import { describe, expect, it, vi } from "vitest";
import {
  collectEntryFiles,
  directoryDropEntries,
  folderPathFromRelative,
  isJunkPath,
  isUnsupportedError,
  partitionFolderFiles,
  readAllEntries,
  runWithConcurrency,
  summarizeOutcomes,
} from "../folderUpload";

// ── Mock FileSystem entries ──────────────────────────────────────────────────

function fileEntry(name: string): FileSystemFileEntry {
  return {
    name,
    isFile: true,
    isDirectory: false,
    file: (ok: (f: File) => void) => ok(new File(["x"], name)),
  } as unknown as FileSystemFileEntry;
}

/** A directory whose reader pages its children `pageSize` at a time, then returns []. */
function dirEntry(name: string, children: FileSystemEntry[], pageSize = 2) {
  const readEntries = vi.fn();
  const entry = {
    name,
    isFile: false,
    isDirectory: true,
    createReader: () => {
      let offset = 0;
      readEntries.mockImplementation((ok: (e: FileSystemEntry[]) => void) => {
        const batch = children.slice(offset, offset + pageSize);
        offset += pageSize;
        ok(batch);
      });
      return { readEntries } as unknown as FileSystemDirectoryReader;
    },
  } as unknown as FileSystemDirectoryEntry;
  return { entry, readEntries };
}

describe("folderPathFromRelative", () => {
  it("takes the directory part of a nested path", () => {
    expect(folderPathFromRelative("Reports/2026/q1.pdf")).toBe("Reports/2026");
  });
  it("uses the top folder for a file at the root of the chosen folder", () => {
    expect(folderPathFromRelative("Reports/q1.pdf")).toBe("Reports");
  });
  it("is empty for a loose file", () => {
    expect(folderPathFromRelative("q1.pdf")).toBe("");
  });
  it("normalises backslashes", () => {
    expect(folderPathFromRelative("A\\B\\c.txt")).toBe("A/B");
  });
});

describe("junk filtering", () => {
  it.each([
    ["Reports/.DS_Store", true],
    ["Reports/.git/config", true],
    ["proj/node_modules/x/index.js", true],
    [".hidden/a.pdf", true],
    ["Reports/2026/q1.pdf", false],
    ["Reports/my.file.pdf", false],
  ])("%s → junk=%s", (path, junk) => {
    expect(isJunkPath(path)).toBe(junk);
  });

  it("partitions into keep and junk", () => {
    const mk = (p: string) => ({ file: new File(["x"], p.split("/").pop()!), relativePath: p });
    const { keep, junk } = partitionFolderFiles([mk("R/a.pdf"), mk("R/.DS_Store"), mk("R/node_modules/b.js")]);
    expect(keep.map((f) => f.relativePath)).toEqual(["R/a.pdf"]);
    expect(junk).toHaveLength(2);
  });
});

describe("readAllEntries", () => {
  it("calls readEntries until an empty batch (paging)", async () => {
    const kids = ["a", "b", "c", "d", "e"].map((n) => fileEntry(`${n}.pdf`));
    const { entry, readEntries } = dirEntry("R", kids, 2);
    const all = await readAllEntries(entry.createReader());
    expect(all.map((e) => e.name)).toEqual(["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"]);
    // 3 non-empty pages + the terminating empty one
    expect(readEntries).toHaveBeenCalledTimes(4);
  });
});

describe("collectEntryFiles", () => {
  it("recurses directories and builds relative paths", async () => {
    const inner = dirEntry("2026", [fileEntry("q1.pdf"), fileEntry("q2.pdf"), fileEntry("q3.pdf")]).entry;
    const root = dirEntry("Reports", [fileEntry("index.md"), inner]).entry;
    const files = await collectEntryFiles(root);
    expect(files.map((f) => f.relativePath).sort()).toEqual([
      "Reports/2026/q1.pdf",
      "Reports/2026/q2.pdf",
      "Reports/2026/q3.pdf",
      "Reports/index.md",
    ]);
  });
});

describe("directoryDropEntries", () => {
  const item = (entry: FileSystemEntry) =>
    ({ kind: "file", webkitGetAsEntry: () => entry }) as unknown as DataTransferItem;

  it("returns entries when a directory is dropped", () => {
    const dt = { items: [item(dirEntry("R", []).entry), item(fileEntry("a.pdf"))] } as unknown as DataTransfer;
    expect(directoryDropEntries(dt)).toHaveLength(2);
  });
  it("returns null for plain files (keeps today's behaviour)", () => {
    const dt = { items: [item(fileEntry("a.pdf"))] } as unknown as DataTransfer;
    expect(directoryDropEntries(dt)).toBeNull();
  });
});

describe("runWithConcurrency", () => {
  it("never exceeds the limit and preserves order", async () => {
    let inFlight = 0;
    let peak = 0;
    const out = await runWithConcurrency([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await new Promise((r) => setTimeout(r, 1));
      inFlight--;
      return n * 10;
    });
    expect(peak).toBe(3);
    expect(out).toEqual([10, 20, 30, 40, 50, 60, 70]);
  });
});

describe("summarizeOutcomes", () => {
  it("counts uploaded, unchanged, skipped (unsupported + junk) and failed", () => {
    const s = summarizeOutcomes(
      [
        { name: "R/a.pdf", status: "uploaded" },
        { name: "R/b.pdf", status: "uploaded" },
        { name: "R/c.pdf", status: "unchanged" },
        { name: "R/d.exe", status: "unsupported", error: "Unsupported file type" },
        { name: "R/e.pdf", status: "failed", error: "Upload failed (500)" },
      ],
      2,
    );
    expect(s).toEqual({
      uploaded: 2,
      unchanged: 1,
      skipped: 3,
      failed: 1,
      failedNames: [{ name: "R/e.pdf", error: "Upload failed (500)" }],
    });
  });

  it("classifies only a 400 naming an unsupported type as skipped", () => {
    expect(isUnsupportedError(400, "Unsupported file type: .exe")).toBe(true);
    expect(isUnsupportedError(400, "folder_path must not contain '..'")).toBe(false);
    expect(isUnsupportedError(500, "unsupported")).toBe(false);
  });
});
