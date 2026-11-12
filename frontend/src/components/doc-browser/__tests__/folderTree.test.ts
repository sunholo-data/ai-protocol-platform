import { describe, expect, it } from "vitest";
import type { Folder } from "@/hooks/useDocBrowser";
import {
  buildFolderTree,
  folderSegments,
  parentPath,
  subtreeContains,
  subtreeDocCount,
  subtreeProjects,
  syncedSourceFor,
  type FolderTreeNode,
} from "../folderTree";

function folder(id: string, name: string, over: Partial<Folder> = {}): Folder {
  return { id, name, userId: "u1", docCount: 1, parsedCount: 1, ...over };
}

/** `label[*]` per node (`*` = parent only), children in brackets. */
function shape(nodes: FolderTreeNode[]): string[] {
  return nodes.map((n) => {
    const own = `${n.label}${n.folder ? "" : "*"}`;
    return n.children.length ? `${own}[${shape(n.children).join(",")}]` : own;
  });
}

describe("folderSegments", () => {
  it("drops empty and blank segments", () => {
    expect(folderSegments("a//b/ ")).toEqual(["a", "b"]);
    expect(folderSegments("plain")).toEqual(["plain"]);
  });
});

describe("buildFolderTree", () => {
  it("nests an uploaded folder's subfolders under it", () => {
    const tree = buildFolderTree([
      folder("1", "flying-fish/survey/2026"),
      folder("2", "flying-fish"),
      folder("3", "flying-fish/survey"),
      folder("4", "flying-fish/notes"),
    ]);
    expect(shape(tree)).toEqual(["flying-fish[notes,survey[2026]]"]);
    expect(tree[0].folder?.id).toBe("2");
  });

  it("adds a parent-only row when the top folder had no loose files", () => {
    const tree = buildFolderTree([folder("1", "flying-fish/a"), folder("2", "flying-fish/b")]);
    expect(shape(tree)).toEqual(["flying-fish*[a,b]"]);
    expect(tree[0].path).toBe("flying-fish");
    expect(subtreeDocCount(tree[0])).toBe(2);
  });

  it("keeps the top-level order and sorts subfolders by name", () => {
    const tree = buildFolderTree([
      folder("1", "Uploads"),
      folder("2", "fish/b10"),
      folder("3", "Contracts"),
      folder("4", "fish/b2"),
    ]);
    expect(shape(tree)).toEqual(["Uploads", "fish*[b2,b10]", "Contracts"]);
  });

  it("never nests under or inside the assistant's own folder", () => {
    const tree = buildFolderTree([
      folder("c", "Created", { system: true }),
      folder("1", "Created/x"),
    ]);
    expect(shape(tree)).toEqual(["Created", "Created*[x]"]);
    expect(tree[0].folder?.system).toBe(true);
  });

  it("shows both folders when two share a name, with distinct keys", () => {
    const tree = buildFolderTree([folder("1", "dup"), folder("2", "dup")]);
    expect(tree.map((n) => n.folder?.id)).toEqual(["1", "2"]);
    expect(new Set(tree.map((n) => n.key)).size).toBe(2);
  });

  it("finds a folder anywhere below a node", () => {
    const [root] = buildFolderTree([folder("1", "a/b/c"), folder("2", "a")]);
    expect(subtreeContains(root, "1")).toBe(true);
    expect(subtreeContains(root, "2")).toBe(true);
    expect(subtreeContains(root, "nope")).toBe(false);
    expect(subtreeContains(root, null)).toBe(false);
  });
});

describe("syncedSourceFor", () => {
  const src = { prefix: "client/docs", projectId: "p1", projectName: "AIPLA", repo: "example-org/app" };

  it("matches the synced folder, anything under it and anything above it", () => {
    for (const name of ["client/docs", "Client/Docs", "client/docs/a/b", "client"]) {
      expect(syncedSourceFor([src], name)).toBe(src);
    }
  });

  it("leaves look-alike and unrelated folders alone", () => {
    for (const name of ["", "client/docs2", "clientele", "docs", "other/client/docs"]) {
      expect(syncedSourceFor([src], name)).toBeNull();
    }
  });
});

describe("parentPath", () => {
  it("drops the last segment", () => {
    expect(parentPath("a/b/c")).toBe("a/b");
    expect(parentPath("a")).toBe("");
  });
});

describe("subtreeProjects", () => {
  it("sums a project across the folder and its subfolders; a source link wins", () => {
    const [root] = buildFolderTree([folder("r", "docs"), folder("a", "docs/a"), folder("b", "docs/b")]);
    const links = {
      r: [{ projectId: "p1", projectName: "beta", documents: 2, via: "documents" as const }],
      a: [
        { projectId: "p1", projectName: "beta", documents: 3, via: "github" as const },
        { projectId: "p2", projectName: "Alpha", documents: 1, via: "documents" as const },
      ],
    };
    expect(subtreeProjects(root, links)).toEqual([
      { projectId: "p2", projectName: "Alpha", documents: 1, via: "documents" },
      { projectId: "p1", projectName: "beta", documents: 5, via: "github" },
    ]);
    expect(subtreeProjects(root.children[1], links)).toEqual([]);
    expect(links.r[0].documents).toBe(2); // inputs untouched
  });
});
