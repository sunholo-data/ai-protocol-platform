"use client";

import type { ParsedDocument } from "@/hooks/useDocBrowser";
import { useDocBrowser } from "@/hooks/useDocBrowser";
import { useKnowledgeBases } from "@/hooks/useKnowledgeBases";
import { DocListFolder } from "./DocListFolder";
import { DocListSearch } from "./DocListSearch";
import { DocParseProgress } from "./DocParseProgress";

interface DocListViewProps {
  uid: string;
  /** The skill being chatted with — offered first when adding a folder to a skill. */
  skillId?: string;
  onDocClick?: (doc: ParsedDocument) => void;
}

export function DocListView({ uid, skillId, onDocClick }: DocListViewProps) {
  const {
    folders,
    activeFolderId,
    documents,
    searchQuery,
    filteredDocuments,
    setActiveFolderId,
    setSearchQuery,
    loading,
    error,
    retry,
  } = useDocBrowser(uid);
  const knowledge = useKnowledgeBases();

  // Stats for active folder
  const activeFolder = folders.find((f) => f.id === activeFolderId) ?? null;
  const parsedCount = documents.filter((d) => d.parseStatus === "parsed").length;
  const failedCount = documents.filter((d) => d.parseStatus === "failed").length;
  const docCount = documents.length;

  function handleDocClick(doc: ParsedDocument) {
    onDocClick?.(doc);
  }

  // A deleted active folder must not leave the view pointing at nothing:
  // select the next folder the user owns (else any remaining one).
  function handleFolderDeleted(folderId: string) {
    if (folderId !== activeFolderId) return;
    const rest = folders.filter((f) => f.id !== folderId);
    setActiveFolderId(rest.find((f) => !f.system)?.id ?? rest[0]?.id ?? null);
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-xs font-semibold text-foreground">My Documents</span>
        {folders.length > 0 && (
          <span className="text-[10px] text-muted-foreground">
            {folders.length} folder{folders.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      <DocListSearch value={searchQuery} onChange={setSearchQuery} />

      <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
        {error && (
          <div role="alert" className="mx-2 my-2 rounded border border-destructive/40 px-2 py-1.5 text-xs text-destructive">
            <span>{error}</span>{" "}
            <button type="button" onClick={retry} className="font-medium underline underline-offset-2">
              Retry
            </button>
          </div>
        )}

        {loading && folders.length === 0 && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">Loading documents…</p>
        )}

        {!loading && !error && folders.length === 0 && (
          <p className="px-3 py-4 text-center text-xs text-muted-foreground">
            No documents yet.
            <br />
            Upload a file to get started.
          </p>
        )}

        {searchQuery.trim() !== "" ? (
          // Flat search results across all docs in the active folder
          <div className="space-y-0.5 px-1">
            {filteredDocuments.length === 0 ? (
              <p className="py-3 text-center text-xs text-muted-foreground">
                No results for &ldquo;{searchQuery}&rdquo;
              </p>
            ) : (
              filteredDocuments.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  onClick={() => handleDocClick(doc)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent"
                >
                  <span className="min-w-0 flex-1 truncate">{doc.originalFilename}</span>
                </button>
              ))
            )}
          </div>
        ) : (
          // Folder accordion view
          <div className="space-y-0.5 px-1">
            {folders.map((folder) => (
              <DocListFolder
                key={folder.id}
                folder={folder}
                documents={folder.id === activeFolderId ? filteredDocuments : []}
                isActive={folder.id === activeFolderId}
                onSelect={setActiveFolderId}
                onDocClick={handleDocClick}
                folders={folders}
                onDeleted={handleFolderDeleted}
                searchAvailable={knowledge.available === true}
                knowledgeBase={folder.knowledgeBaseId ? knowledge.byId[folder.knowledgeBaseId] : undefined}
                uid={uid}
                currentSkillId={skillId}
              />
            ))}
          </div>
        )}
      </div>

      {activeFolder && (
        <DocParseProgress parsedCount={parsedCount} failedCount={failedCount} docCount={docCount} />
      )}
    </div>
  );
}
