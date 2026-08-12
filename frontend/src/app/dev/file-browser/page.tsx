/**
 * Dev fixture page — File Browser component review
 *
 * Renders all doc-browser components with pre-built dummy data:
 *
 *   1. DocListView   — folder accordion with mixed parse-status docs, search
 *   2. DocTabsBar    — three open tabs, active/inactive styling, toggle
 *   3. UploadDropZone — drop target UI (upload calls mocked)
 *   4. DocParseProgress — progress bar at various completion %
 *
 * Accessible at http://localhost:3456/dev/file-browser (dev only — no auth, no Firestore).
 * All fixture data is hard-coded here.
 */

"use client";

import { useState } from "react";
import { DocListFolder } from "@/components/doc-browser/DocListFolder";
import { DocListItem } from "@/components/doc-browser/DocListItem";
import { DocListSearch } from "@/components/doc-browser/DocListSearch";
import { DocParseProgress } from "@/components/doc-browser/DocParseProgress";
import type { DocTabData } from "@/components/doc-browser/DocTab";
import { DocTab } from "@/components/doc-browser/DocTab";
import { DocTabsBar } from "@/components/doc-browser/DocTabsBar";
import { UploadDropZone } from "@/components/doc-browser/UploadDropZone";
import type { Folder, ParsedDocument } from "@/hooks/useDocBrowser";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FOLDERS: Folder[] = [
  { id: "f1", name: "Q1 Financial Review", userId: "demo", docCount: 5, parsedCount: 3 },
  { id: "f2", name: "Product Roadmap 2026", userId: "demo", docCount: 3, parsedCount: 3 },
  { id: "f3", name: "Legal Contracts", userId: "demo", docCount: 2, parsedCount: 0 },
];

const DOCS_F1: ParsedDocument[] = [
  { id: "d1", originalFilename: "Q1-Executive-Summary.docx", sourceFormat: "docx", parseError: null, parseStatus: "parsed", folderId: "f1", userId: "demo", blockCount: 42, hasA2ui: true },
  { id: "d2", originalFilename: "Q1-Financial-Model.xlsx", sourceFormat: "xlsx", parseError: null, parseStatus: "parsed", folderId: "f1", userId: "demo", blockCount: 18, hasA2ui: true },
  { id: "d3", originalFilename: "Q1-Investor-Deck.pptx", sourceFormat: "pptx", parseError: null, parseStatus: "parsed", folderId: "f1", userId: "demo", blockCount: 24, hasA2ui: true },
  { id: "d4", originalFilename: "Q1-Cash-Flow-Analysis.pdf", sourceFormat: "pdf", parseError: null, parseStatus: "pending_ai_extraction", folderId: "f1", userId: "demo", blockCount: null, hasA2ui: false },
  { id: "d5", originalFilename: "Q1-Audit-Report.pdf", sourceFormat: "pdf", parseError: null, parseStatus: "pending", folderId: "f1", userId: "demo", blockCount: null, hasA2ui: false },
];

const DOCS_F2: ParsedDocument[] = [
  { id: "d6", originalFilename: "Roadmap-2026-H1.md", sourceFormat: "md", parseError: null, parseStatus: "parsed", folderId: "f2", userId: "demo", blockCount: 67, hasA2ui: true },
  { id: "d7", originalFilename: "Feature-Priorities.csv", sourceFormat: "csv", parseError: null, parseStatus: "parsed", folderId: "f2", userId: "demo", blockCount: 8, hasA2ui: true },
  { id: "d8", originalFilename: "OKRs-2026.docx", sourceFormat: "docx", parseError: null, parseStatus: "parsed", folderId: "f2", userId: "demo", blockCount: 31, hasA2ui: true },
];

const DOCS_F3: ParsedDocument[] = [
  { id: "d9", originalFilename: "NDA-Acme-Corp.pdf", sourceFormat: "pdf", parseError: null, parseStatus: "pending", folderId: "f3", userId: "demo", blockCount: null, hasA2ui: false },
  { id: "d10", originalFilename: "SLA-2026.pdf", sourceFormat: "pdf", parseError: "Parse failed", parseStatus: "failed", folderId: "f3", userId: "demo", blockCount: null, hasA2ui: false },
];

const ALL_DOCS: Record<string, ParsedDocument[]> = { f1: DOCS_F1, f2: DOCS_F2, f3: DOCS_F3 };

const INITIAL_TABS: DocTabData[] = [
  { id: "d1", filename: "Q1-Executive-Summary.docx", format: "docx", included: true },
  { id: "d6", filename: "Roadmap-2026-H1.md", format: "md", included: true },
  { id: "d7", filename: "Feature-Priorities.csv", format: "csv", included: true },
];

// ---------------------------------------------------------------------------
// Section wrapper
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="border-b pb-1 text-sm font-semibold text-foreground">{title}</h2>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FileBrowserDevPage() {
  const [activeFolder, setActiveFolder] = useState<string>("f1");
  const [search, setSearch] = useState("");
  const [tabs, setTabs] = useState<DocTabData[]>(INITIAL_TABS);
  const [activeTabId, setActiveTabId] = useState<string>("d1");
  const [showBrowser, setShowBrowser] = useState(true);
  const [log, setLog] = useState<string[]>([]);

  function logEvent(msg: string) {
    setLog((prev) => [`${new Date().toISOString().slice(11, 19)} ${msg}`, ...prev.slice(0, 9)]);
  }

  const activeDocs = ALL_DOCS[activeFolder] ?? [];
  const lower = search.toLowerCase();
  const filtered = search ? activeDocs.filter((d) => d.originalFilename.toLowerCase().includes(lower)) : activeDocs;
  const parsedCount = activeDocs.filter((d) => d.parseStatus === "parsed").length;

  function handleDocClick(doc: ParsedDocument) {
    logEvent(`Clicked: ${doc.originalFilename}`);
    setTabs((prev) => {
      if (prev.find((t) => t.id === doc.id)) return prev;
      return [
        ...prev,
        { id: doc.id, filename: doc.originalFilename, format: doc.sourceFormat, included: true },
      ];
    });
    setActiveTabId(doc.id);
  }

  function handleTabToggleInclude(id: string) {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, included: !t.included } : t)));
    logEvent(`Toggled include: ${id}`);
  }

  function handleTabClose(id: string) {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeTabId === id) setActiveTabId(next[next.length - 1]?.id ?? "");
      return next;
    });
    logEvent(`Closed tab: ${id}`);
  }

  return (
    <div className="min-h-screen bg-background p-6 text-sm">
      <div className="mx-auto max-w-6xl space-y-8">
        <div>
          <h1 className="text-xl font-bold">File Browser — Dev Fixture</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            All components wired with dummy data. No Firestore, no auth required.
            <span className="ml-2 text-teal-600">localhost:3456/dev/file-browser</span>
          </p>
        </div>

        {/* Event log */}
        <div className="rounded-md border bg-muted/30 p-3">
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Event log</p>
          {log.length === 0 ? (
            <p className="text-xs text-muted-foreground">Interact with components below…</p>
          ) : (
            <div className="space-y-0.5 font-mono text-xs">
              {log.map((l, i) => <p key={i}>{l}</p>)}
            </div>
          )}
        </div>

        {/* ================================================================ */}
        <Section title="1 — DocTabsBar (3 tabs, toggleable browser)">
          <div className="rounded-md border">
            <DocTabsBar
              tabs={tabs}
              activeTabId={activeTabId}
              showBrowser={showBrowser}
              onSelect={(id) => { setActiveTabId(id); logEvent(`Tab selected: ${id}`); }}
              onClose={handleTabClose}
              onToggleInclude={handleTabToggleInclude}
              onToggleBrowser={() => { setShowBrowser((v) => !v); logEvent("Toggled browser"); }}
            />
            <div className="p-3 text-xs text-muted-foreground">
              Active tab: <strong>{activeTabId || "none"}</strong> · Browser visible: <strong>{String(showBrowser)}</strong> · {tabs.length} open tab(s)
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Click tabs to activate · × to close · grid icon toggles browser
          </p>
        </Section>

        {/* ================================================================ */}
        <Section title="2 — DocTab variants">
          <div className="flex flex-wrap gap-2">
            {[
              { id: "t1", filename: "Executive-Summary.docx", format: "docx", included: true },
              { id: "t2", filename: "Budget-Model.xlsx", format: "xlsx", included: true },
              { id: "t3", filename: "Investor-Deck.pptx", format: "pptx", included: false },
              { id: "t4", filename: "Audit-Report.pdf", format: "pdf", included: true },
              { id: "t5", filename: "README.md", format: "md", included: true },
              { id: "t6", filename: "Data-Export.csv", format: "csv", included: true },
            ].map((tab, i) => (
              <DocTab
                key={tab.id}
                tab={tab}
                isActive={i === 0}
                onSelect={(id) => logEvent(`Tab selected: ${id}`)}
                onClose={(id) => logEvent(`Tab closed: ${id}`)}
                onToggleInclude={(id) => logEvent(`Toggled include: ${id}`)}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            First tab shown active (border-primary). Hover to reveal × close button.
          </p>
        </Section>

        {/* ================================================================ */}
        <Section title="3 — DocListItem — all parse-status variants">
          <div className="w-64 rounded-md border bg-background p-1">
            {[
              { parseStatus: "parsed" as const, parseError: null, originalFilename: "Summary.docx", sourceFormat: "docx", blockCount: 42, hasA2ui: true },
              { parseStatus: "pending" as const, parseError: null, originalFilename: "Uploading-Now.pdf", sourceFormat: "pdf", blockCount: null, hasA2ui: false },
              { parseStatus: "pending_ai_extraction" as const, parseError: null, originalFilename: "Scanned-Contract.pdf", sourceFormat: "pdf", blockCount: null, hasA2ui: false },
              { parseStatus: "failed" as const, parseError: "AILANG Parse API error: 500 writeFile: is a directory", originalFilename: "Corrupt-File.xlsx", sourceFormat: "xlsx", blockCount: null, hasA2ui: false },
            ].map((d, i) => (
              <DocListItem
                key={i}
                doc={{ id: `status-${i}`, folderId: "f1", userId: "demo", createdAt: undefined, ...d }}
                onClick={(doc) => logEvent(`Clicked: ${doc.originalFilename} (${doc.parseStatus})`)}
              />
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Green = parsed · amber pulse = pending · sky pulse = pending_ai_extraction · red = failed
          </p>
        </Section>

        {/* ================================================================ */}
        <Section title="4 — DocListSearch">
          <div className="w-64 rounded-md border bg-background">
            <DocListSearch value={search} onChange={(q) => { setSearch(q); logEvent(`Search: "${q}"`); }} />
          </div>
          <p className="text-xs text-muted-foreground">Search value: <strong>{search || "(empty)"}</strong></p>
        </Section>

        {/* ================================================================ */}
        <Section title="5 — DocListFolder (Q1 Financial Review — 5 docs, 3 parsed)">
          <div className="w-72 rounded-md border bg-background p-1">
            <DocListFolder
              folder={FOLDERS[0]}
              documents={filtered}
              isActive={activeFolder === "f1"}
              onSelect={(id) => { setActiveFolder(id); logEvent(`Folder selected: ${id}`); }}
              onDocClick={handleDocClick}
            />
            <DocListFolder
              folder={FOLDERS[1]}
              documents={activeFolder === "f2" ? DOCS_F2 : []}
              isActive={activeFolder === "f2"}
              onSelect={(id) => { setActiveFolder(id); logEvent(`Folder selected: ${id}`); }}
              onDocClick={handleDocClick}
            />
            <DocListFolder
              folder={FOLDERS[2]}
              documents={activeFolder === "f3" ? DOCS_F3 : []}
              isActive={activeFolder === "f3"}
              onSelect={(id) => { setActiveFolder(id); logEvent(`Folder selected: ${id}`); }}
              onDocClick={handleDocClick}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Click a folder to expand · click a file to open tab · search box above filters
          </p>
        </Section>

        {/* ================================================================ */}
        <Section title="6 — DocParseProgress — various completion levels">
          <div className="w-72 space-y-2 rounded-md border bg-background">
            <div className="border-b p-2 text-xs font-semibold text-muted-foreground">0% (0/5)</div>
            <DocParseProgress parsedCount={0} failedCount={2} docCount={5} />
            <div className="border-b border-t p-2 text-xs font-semibold text-muted-foreground">40% (2/5)</div>
            <DocParseProgress parsedCount={2} failedCount={1} docCount={5} />
            <div className="border-b border-t p-2 text-xs font-semibold text-muted-foreground">80% (4/5)</div>
            <DocParseProgress parsedCount={4} failedCount={0} docCount={5} />
            <div className="border-b border-t p-2 text-xs font-semibold text-muted-foreground">100% (hidden — all done)</div>
            <DocParseProgress parsedCount={5} failedCount={0} docCount={5} />
            <div className="p-2 text-xs text-muted-foreground italic">(no bar rendered above when complete)</div>
          </div>
          <p className="text-xs text-muted-foreground">Bar hidden when parsedCount ≥ docCount or docCount is 0</p>
        </Section>

        {/* ================================================================ */}
        <Section title="7 — UploadDropZone (mocked — no real backend)">
          <div className="w-80 rounded-md border bg-background">
            <UploadDropZone
              folderId="demo-folder"
              onUploadComplete={(docId, filename) => logEvent(`Upload complete: ${filename} → ${docId}`)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Drag files onto the zone or click to browse. XHR will fail (no backend) — check event log.
          </p>
        </Section>

        {/* ================================================================ */}
        <Section title="8 — Full sidebar simulation">
          <div className="flex h-[520px] overflow-hidden rounded-md border">
            <aside className="flex w-64 shrink-0 flex-col border-r bg-muted/30">
              <div className="flex items-center justify-between border-b px-3 py-2">
                <span className="text-xs font-semibold">My Documents</span>
                <span className="text-[10px] text-muted-foreground">3 folders</span>
              </div>
              <DocListSearch value={search} onChange={setSearch} />
              <div className="min-h-0 flex-1 overflow-y-auto px-1 py-1">
                {FOLDERS.map((folder) => (
                  <DocListFolder
                    key={folder.id}
                    folder={folder}
                    documents={folder.id === activeFolder ? filtered : []}
                    isActive={folder.id === activeFolder}
                    onSelect={(id) => { setActiveFolder(id); }}
                    onDocClick={handleDocClick}
                  />
                ))}
              </div>
              <DocParseProgress parsedCount={parsedCount} failedCount={activeDocs.filter((d) => d.parseStatus === "failed").length} docCount={activeDocs.length} />
            </aside>
            <div className="flex min-w-0 flex-1 flex-col">
              <DocTabsBar
                tabs={tabs}
                activeTabId={activeTabId}
                showBrowser={showBrowser}
                onSelect={setActiveTabId}
                onClose={handleTabClose}
                onToggleInclude={handleTabToggleInclude}
                onToggleBrowser={() => setShowBrowser((v) => !v)}
              />
              <div className="flex-1 p-4 text-xs text-muted-foreground">
                {activeTabId ? (
                  <p>Viewing: <strong>{tabs.find((t) => t.id === activeTabId)?.filename}</strong><br />
                    (A2UI content would render here)</p>
                ) : (
                  <p>No document open — click a file in the sidebar</p>
                )}
              </div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Full sidebar + tabs wired together. Toggle sidebar with the grid icon in DocTabsBar.
          </p>
        </Section>
      </div>
    </div>
  );
}
