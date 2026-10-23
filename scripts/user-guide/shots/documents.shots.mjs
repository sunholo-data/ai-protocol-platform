// Screenshots for docs/user-guide/documents.md. File names match the page's image references.
// Synthetic data only (see lib.mjs). Page-specific answers live in `fixture` below.
import { json } from "./lib.mjs";
import { UID } from "./fixtures.mjs";

const now = Date.now();
const iso = (minsAgo) => new Date(now - minsAgo * 60000).toISOString();
const doc = (id, folderId, originalFilename, sourceFormat, extra = {}) => ({
  id,
  folderId,
  originalFilename,
  sourceFormat,
  source: "uploaded",
  parseStatus: "parsed",
  parseError: null,
  userId: UID,
  blockCount: 24,
  createdAt: iso(20),
  updatedAt: iso(20),
  ...extra,
});

const PHOENIX = [
  doc("d1", "f-phoenix", "Phoenix project brief.pdf", "pdf"),
  doc("d3", "f-phoenix", "Kick-off meeting notes.docx", "docx"),
  doc("d10", "f-phoenix", "Supplier shortlist.xlsx", "xlsx", { parseStatus: "pending", blockCount: 0 }),
  doc("d11", "f-phoenix", "Scanned site survey.pdf", "pdf", {
    parseStatus: "failed",
    parseError: "No readable text — the file looks like a scan.",
    blockCount: 0,
  }),
];
const CREATED = [doc("d8", "f-created", "Kick-off summary.md", "md", { source: "generated" })];

const BLOCKS = [
  { type: "heading", level: 1, text: "Project Phoenix — kick-off meeting" },
  { type: "text", text: "Attendees: project lead, finance, two site engineers and the supplier liaison." },
  { type: "heading", level: 2, text: "Decisions" },
  {
    type: "list",
    items: [
      "Phase one starts on 3 March with the northern site.",
      "Weekly status notes go to the steering group every Friday.",
      "The supplier shortlist is reviewed before the next meeting.",
    ],
  },
  { type: "heading", level: 2, text: "Budget" },
  {
    type: "table",
    headers: [{ text: "Item" }, { text: "Estimate" }],
    rows: [
      { cells: [{ text: "Site preparation" }, { text: "120,000" }] },
      { cells: [{ text: "Equipment" }, { text: "340,000" }] },
      { cells: [{ text: "Contingency" }, { text: "45,000" }] },
    ],
  },
  { type: "heading", level: 2, text: "Next steps" },
  {
    type: "text",
    text:
      "The team agreed that the risk register will be updated before the end of the month, and that any change to the schedule is raised with the project lead first so the steering group hears about it in one place.",
  },
];

const detail = (d) => ({
  ...d,
  sourceUrl: null,
  parsedAt: iso(19),
  summary: { totalBlocks: BLOCKS.length, headings: 4, tables: 1, images: 0, changes: 0 },
  blocks: d.parseStatus === "parsed" ? BLOCKS : [],
  template: null,
});

export function fixture() {
  return async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace("/api/proxy", "");
    const m = req.method();
    let mm;
    if (p === "/api/folders/f-phoenix/documents") return json(route, 200, { documents: PHOENIX }), true;
    if (p === "/api/folders/f-created/documents") return json(route, 200, { documents: CREATED }), true;
    if ((mm = p.match(/^\/api\/documents\/([^/]+)$/)) && m === "GET") {
      const d = [...PHOENIX, ...CREATED].find((x) => x.id === mm[1]);
      if (d) return json(route, 200, detail(d)), true;
    }
    if (/^\/api\/documents\/[^/]+\/sessions$/.test(p)) return json(route, 200, { sessions: [] }), true;
    if ((mm = p.match(/^\/api\/documents\/([^/]+)\/versions$/))) {
      return json(route, 200, { docId: mm[1], filename: "Kick-off meeting notes.docx", currentVersion: 2, versions: [1, 2] }), true;
    }
    return false;
  };
}

export default async function documents({ page, base, shot, tidy, around }) {
  await page.goto(`${base}/chat/@aitana-platform/demo-researcher`, { waitUntil: "networkidle", timeout: 90000 });
  await tidy();
  await page.waitForTimeout(1200);

  // Upload: the drop area on a fresh chat, and the attach button in the message box
  const startFrom = page.getByText("Or start from a document").locator("xpath=ancestor::div[contains(@class,'max-w-2xl')][1]");
  await shot("01-start-from-a-document", startFrom);
  const attach = page.getByRole("button", { name: "Attach a document" }).first();
  const message = page.getByRole("textbox").first();
  await attach.hover();
  await page.waitForTimeout(600);
  await shot("02-attach-a-document", "page", { clip: await union(page, [message, attach], 24) });

  // The sidebar: status dots, failed parse with Retry
  await page.setViewportSize({ width: 1280, height: 1500 });
  const show = page.getByRole("button", { name: "Show sidebar" });
  if (await show.count()) await show.first().click();
  const sidebar = () => page.locator("aside").filter({ hasText: "Your files" }).first();
  const crop = around(sidebar);
  const folderRow = (name) => page.locator("div.group\\/folder").filter({ hasText: name }).first();
  const docRow = (name) =>
    page.getByText(name, { exact: true }).first().locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " group ")][1]');
  await page.getByText("Project Phoenix").first().waitFor({ timeout: 30000 });
  if (!(await page.getByText("Scanned site survey.pdf").count())) await folderRow("Project Phoenix").getByRole("button").first().click();
  await page.getByText("Scanned site survey.pdf").first().waitFor();
  await crop("03-parse-status", folderRow("Project Phoenix"), docRow("Scanned site survey.pdf"));

  // (The hover preview is described in the page text: its popover is not a stable screenshot.)

  // Open it: the Document tab, and "Will process" above the message box
  await page.mouse.move(900, 80);
  await page.getByText("Kick-off meeting notes.docx", { exact: true }).first().click();
  // The Text view: the parsed structure (the Styled view renders the original file).
  await page.getByRole("button", { name: "Text", exact: true }).first().click();
  await page.getByText("Project Phoenix — kick-off meeting").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1200);
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.waitForTimeout(800);
  await shot("05-document-open");
  const tab = page.getByRole("tab").filter({ hasText: "Kick-off meeting notes.docx" }).first();
  await shot("06-in-this-conversation", "page", { clip: await union(page, [tab], 14) });

  // Edit a passage: select a sentence
  const para = page.getByText(/The team agreed that the risk register/).first();
  await para.evaluate((el) => {
    const range = document.createRange();
    const node = el.firstChild ?? el;
    range.setStart(node, 0);
    range.setEnd(node, Math.min(82, (node.textContent ?? "").length));
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    document.dispatchEvent(new Event("selectionchange"));
  });
  const box = page.getByRole("textbox", { name: "What should change about this passage" });
  await box.waitFor({ timeout: 10000 });
  await box.fill("Make it shorter and clearer");
  await shot("07-edit-a-passage", "page", { clip: await union(page, [para, box], 40) });

  // Download and History
  await page.getByRole("button", { name: /History/ }).first().click();
  await page.waitForTimeout(1000);
  const download = page.getByRole("button", { name: /Download Kick-off meeting notes\.docx/ }).first();
  const formatting = page.getByRole("button", { name: /Formatting/ }).first();
  const v1 = page.getByRole("button", { name: "Restore version 1" }).first();
  await v1.waitFor({ timeout: 10000 });
  const title = page.locator('span[title="Kick-off meeting notes.docx"]').first();
  await shot("08-download-and-history", "page", { clip: await union(page, [title, formatting, download, v1, page.getByText("Select any text to edit it").first()], 16) });
  await page.getByRole("button", { name: /History/ }).first().click();

  // Rename: the ⋯ menu, then the name becomes a box (Esc to leave it unchanged)
  await page.setViewportSize({ width: 1280, height: 1500 });
  await page.waitForTimeout(600);
  const renameRow = docRow("Supplier shortlist.xlsx");
  await renameRow.hover();
  await renameRow.getByRole("button", { name: "Document actions", exact: true }).first().click();
  const renameItem = page.getByRole("menuitem", { name: "Rename" });
  await renameItem.waitFor();
  await crop("11-rename-menu", renameRow, page.getByRole("menu").first());
  await renameItem.click();
  const nameBox = page.getByRole("textbox", { name: "Document name" });
  await nameBox.waitFor();
  await nameBox.fill("Supplier shortlist Q3");
  await crop("12-rename-box", nameBox.locator("xpath=.."));
  await nameBox.press("Escape");

  // Delete: the bin asks first, naming the document (then Cancel)
  await page.setViewportSize({ width: 1280, height: 1500 });
  await page.waitForTimeout(600);
  const row = docRow("Phoenix project brief.pdf");
  await row.hover();
  await row.locator('button[title="Delete document"]').first().click();
  const confirm = page.getByRole("alertdialog", { name: "Delete document?" });
  await confirm.waitFor();
  await crop("09-delete-confirm", row, confirm);
  await confirm.getByRole("button", { name: "Cancel" }).click();

  // The Created folder
  await page.mouse.move(900, 80);
  await folderRow("Created").getByRole("button").first().click();
  await page.getByText("Kick-off summary.md").first().waitFor();
  await crop("10-created-folder", folderRow("Created"), docRow("Kick-off summary.md"));
}

/** A page clip covering several locators, padded. */
async function union(page, locs, pad = 12) {
  const boxes = (await Promise.all(locs.map((l) => l.boundingBox()))).filter(Boolean);
  const x = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
  const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
  const r = Math.max(...boxes.map((b) => b.x + b.width)) + pad;
  const btm = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
  const vw = page.viewportSize();
  return { x, y, width: Math.min(r, vw.width) - x, height: Math.min(btm, vw.height) - y };
}
