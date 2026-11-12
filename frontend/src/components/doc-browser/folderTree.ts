/**
 * Nest the flat folder list for display.
 *
 * Library folders are flat on the backend: a folder upload names each folder
 * after its relative directory path (`flying-fish/survey/2026`). This turns
 * those names into a tree, so the sidebar shows `flying-fish` once with its
 * subfolders inside it instead of one top-level row per path.
 *
 * A path that only exists as the parent of other folders (an uploaded folder
 * with no loose files of its own) becomes a node with `folder: null`.
 */
import type { Folder } from "@/hooks/useDocBrowser";
import type { FolderProjectLink, FolderProjects } from "@/hooks/useFolderProjects";
import type { SyncedFolder } from "@/hooks/useSyncedFolders";

export interface FolderTreeNode {
  /** Unique among all nodes — the path, plus the folder id for a duplicate name. */
  key: string;
  /** The last path segment, shown as the row's name. */
  label: string;
  /** The normalised full path, e.g. `flying-fish/survey`. */
  path: string;
  /** The real folder at this path, or null when the path is only a parent. */
  folder: Folder | null;
  children: FolderTreeNode[];
}

/** `a//b/ ` → `["a", "b"]`. */
export function folderSegments(name: string): string[] {
  return name
    .split("/")
    .map((s) => s.trim())
    .filter(Boolean);
}

const byLabel = (a: FolderTreeNode, b: FolderTreeNode) =>
  a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: "base" });

/**
 * Build the tree. Top-level rows keep the order of `folders` (the hook sorts
 * newest first; a parent-only row takes the place of its first descendant).
 * Subfolders are sorted by name, like a file browser.
 */
export function buildFolderTree(folders: Folder[]): FolderTreeNode[] {
  const roots: FolderTreeNode[] = [];
  const byPath = new Map<string, FolderTreeNode>();

  function add(siblings: FolderTreeNode[], node: FolderTreeNode) {
    siblings.push(node);
    if (!byPath.has(node.path)) byPath.set(node.path, node);
  }

  for (const folder of folders) {
    const segments = folder.system ? [] : folderSegments(folder.name);
    if (segments.length === 0) {
      // The assistant's own folders (and a nameless one) never nest or parent.
      roots.push({ key: `id:${folder.id}`, label: folder.name, path: folder.name, folder, children: [] });
      continue;
    }

    let siblings = roots;
    for (let i = 1; i < segments.length; i++) {
      const path = segments.slice(0, i).join("/");
      let parent = byPath.get(path);
      if (!parent) {
        parent = { key: `p:${path}`, label: segments[i - 1], path, folder: null, children: [] };
        add(siblings, parent);
      }
      siblings = parent.children;
    }

    const path = segments.join("/");
    const label = segments[segments.length - 1];
    const existing = byPath.get(path);
    if (existing && existing.folder === null) existing.folder = folder;
    else {
      // Two folders can share a name: show both, keyed apart.
      const key = existing ? `p:${path}#${folder.id}` : `p:${path}`;
      add(siblings, { key, label, path, folder, children: [] });
    }
  }

  const sortChildren = (nodes: FolderTreeNode[]) => {
    for (const n of nodes) {
      n.children.sort(byLabel);
      sortChildren(n.children);
    }
  };
  sortChildren(roots);
  return roots;
}

/** Documents in this node and everything under it. */
export function subtreeDocCount(node: FolderTreeNode): number {
  return (node.folder?.docCount ?? 0) + node.children.reduce((n, c) => n + subtreeDocCount(c), 0);
}

/** True when `folderId` is this node's folder or sits anywhere below it. */
export function subtreeContains(node: FolderTreeNode, folderId: string | null): boolean {
  if (!folderId) return false;
  return node.folder?.id === folderId || node.children.some((c) => subtreeContains(c, folderId));
}

/**
 * The GitHub source whose folders `path` is, is inside, or contains — the
 * same rule the server uses to refuse a rename (`FOLDER_SYNCED`).
 */
export function syncedSourceFor(synced: SyncedFolder[], path: string): SyncedFolder | null {
  const name = folderSegments(path).join("/").toLocaleLowerCase();
  if (!name) return null;
  for (const s of synced) {
    const prefix = folderSegments(s.prefix).join("/").toLocaleLowerCase();
    if (name === prefix || name.startsWith(`${prefix}/`) || prefix.startsWith(`${name}/`)) return s;
  }
  return null;
}

/** The parent part of a folder name: `a/b/c` → `a/b`; `a` → ``. */
export function parentPath(name: string): string {
  return folderSegments(name).slice(0, -1).join("/");
}

/**
 * The projects a folder tree is in: this node's links plus its subfolders',
 * summed per project. A source link ("github"/"folder") wins over "documents".
 */
export function subtreeProjects(node: FolderTreeNode, links: FolderProjects): FolderProjectLink[] {
  const merged = new Map<string, FolderProjectLink>();
  const visit = (n: FolderTreeNode) => {
    for (const link of (n.folder && links[n.folder.id]) || []) {
      const seen = merged.get(link.projectId);
      if (!seen) merged.set(link.projectId, { ...link });
      else {
        seen.documents += link.documents;
        if (seen.via === "documents") seen.via = link.via;
      }
    }
    n.children.forEach(visit);
  };
  visit(node);
  return [...merged.values()].sort((a, b) =>
    a.projectName.localeCompare(b.projectName, undefined, { sensitivity: "base" }),
  );
}
