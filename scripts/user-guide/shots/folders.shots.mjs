// Screenshots for docs/user-guide/folders.md. File names match the page's image references.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** A folder to upload: three new files, one already in the library, two the library skips. */
function uploadDir() {
  const dir = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "guide-upload-")), "Quarterly reports");
  fs.mkdirSync(dir);
  for (const f of ["Board pack Q1.pdf", "Revenue by region.xlsx", "Q1 review notes.docx", "Forecast.csv"]) fs.writeFileSync(path.join(dir, f), "synthetic");
  fs.writeFileSync(path.join(dir, ".DS_Store"), "");
  fs.writeFileSync(path.join(dir, "installer.exe"), "");
  return dir;
}

export default async function folders({ page, base, shot, tidy, lib, around }) {
  const open = async (url) => {
    await page.goto(`${base}${url}`, { waitUntil: "networkidle", timeout: 90000 });
    await tidy();
    const show = page.getByRole("button", { name: "Show sidebar" });
    if (await show.count()) await show.first().click();
    await page.getByText("Project Phoenix").first().waitFor({ timeout: 30000 });
    await page.waitForTimeout(800);
  };
  const sidebar = () => page.locator("aside").filter({ hasText: "Your files" }).first();
  const folderRow = (name) => page.locator("div.group\\/folder").filter({ hasText: name }).first();
  // The nearest row around the file name (outer containers also carry `group`).
  const docRow = (name) =>
    page.getByText(name, { exact: true }).first().locator('xpath=ancestor::div[contains(concat(" ", @class, " "), " group ")][1]');
  async function folderMenu(name) {
    const row = folderRow(name);
    await row.hover();
    await row.getByRole("button", { name: "Folder actions" }).click();
    await page.getByRole("menu").waitFor();
  }
  async function docMenu(name) {
    const row = docRow(name);
    await row.hover();
    await row.locator('button[aria-label="Document actions"]:visible').first().click();
    await page.getByText("Move to…").first().click().catch(() => {});
  }

  await open("/chat/@aitana-platform/demo-researcher");
  await shot("01-overview");
  // Taller from here on, so a menu or panel and the rows around it fit one crop.
  await page.setViewportSize({ width: 1280, height: 1500 });
  const crop = around(sidebar);
  const menu = () => page.getByRole("menu");
  const firstFolder = () => page.locator("div.group\\/folder").first();

  // Upload a whole folder
  await page.locator('[data-testid="folder-input"]').first().setInputFiles(uploadDir());
  await page.getByText(/Uploading \d of \d/).first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(1200);
  await crop("03-folder-upload-progress", firstFolder(), page.getByText(/Uploading \d of \d/).first());
  await page.getByText(/^Folder upload:/).first().waitFor({ timeout: 30000 });
  await page.waitForTimeout(1500);
  await crop("04-folder-upload-summary", firstFolder(), page.getByText(/^Folder upload:/).first());

  // Rename
  await folderMenu("Project Phoenix");
  await crop("05-folder-menu", folderRow("Project Phoenix"), menu());
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const input = page.getByRole("textbox", { name: "Folder name" });
  await input.fill("Project Phoenix 2026");
  await crop("06-folder-rename", firstFolder(), input, folderRow("Created"));
  await input.press("Enter");
  await page.getByText("Project Phoenix 2026").first().waitFor();

  // Delete: a folder with documents is kept; an empty one asks first
  await folderMenu("Project Phoenix 2026");
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("alertdialog", { name: "Delete folder?" }).getByRole("button", { name: "Delete" }).click();
  await page.getByText(/still has \d+ documents/).waitFor();
  await crop("07-folder-delete-not-empty", folderRow("Project Phoenix 2026"), page.getByRole("alert").filter({ hasText: "still has" }));
  await page.getByRole("alert").filter({ hasText: "still has" }).getByRole("button", { name: "Dismiss" }).click();
  await folderMenu("Old drafts");
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await crop("08-folder-delete-confirm", folderRow("Old drafts"), page.getByRole("alertdialog", { name: "Delete folder?" }));
  await page.getByRole("alertdialog", { name: "Delete folder?" }).getByRole("button", { name: "Delete" }).click();
  await folderRow("Old drafts").waitFor({ state: "detached" });

  // Move documents
  if (!(await page.getByText("Risk register.csv").count())) await folderRow("Project Phoenix 2026").getByRole("button").first().click();
  await page.getByText("Risk register.csv").first().waitFor();
  await docMenu("Risk register.csv");
  await crop("09-doc-move-menu", docRow("Risk register.csv"), menu());
  await page.getByRole("menuitem", { name: "Move to Market research" }).click();
  await page.waitForTimeout(800);
  await docMenu("Kick-off meeting notes.docx");
  await page.getByRole("menuitem", { name: "Move to Market research" }).click();
  await page.getByRole("alertdialog", { name: "Replace document?" }).waitFor();
  await crop("10-doc-move-conflict", folderRow("Project Phoenix 2026"), page.getByRole("alertdialog", { name: "Replace document?" }));
  await page.getByRole("alertdialog", { name: "Replace document?" }).getByRole("button", { name: "Cancel" }).click();
  const dt = await page.evaluateHandle(() => new DataTransfer());
  const target = folderRow("Market research").locator("..");
  const dragged = page.getByText("Phoenix budget 2026.xlsx", { exact: true }).first().locator('xpath=ancestor::*[@draggable="true"][1]');
  await dragged.dispatchEvent("dragstart", { dataTransfer: dt });
  await target.dispatchEvent("dragenter", { dataTransfer: dt });
  await target.dispatchEvent("dragover", { dataTransfer: dt });
  await crop("11-doc-drag-to-folder", folderRow("Project Phoenix 2026"), folderRow("Market research"));
  await target.dispatchEvent("drop", { dataTransfer: dt });
  await page.waitForTimeout(800);

  // Make searchable
  await folderMenu("Project Phoenix 2026");
  await crop("12-folder-menu-make-searchable", folderRow("Project Phoenix 2026"), menu());
  await page.getByRole("menuitem", { name: "Make searchable" }).click();
  lib.indexed(1);
  await page.getByText(/Indexing 1\/2/).first().waitFor({ timeout: 30000 });
  await crop("13-folder-indexing", firstFolder(), folderRow("Created"));
  lib.indexed(2);
  await page.getByText(/Searchable · 2/).first().waitFor({ timeout: 40000 });
  await crop("14-folder-searchable", firstFolder(), folderRow("Created"));
  await folderMenu("Project Phoenix 2026");
  await crop("15-searchable-folder-menu", folderRow("Project Phoenix 2026"), menu());

  // Add to a skill: wider audience, then a private skill
  await page.getByRole("menuitem", { name: "Add to a skill…" }).click();
  const add = page.getByRole("dialog", { name: "Add this folder to a skill" });
  await add.getByText(/is used by/).waitFor({ timeout: 15000 });
  await crop("16-add-to-skill-widen", folderRow("Project Phoenix 2026"), add);
  await add.getByRole("combobox", { name: "Skill" }).selectOption("phoenix-assistant");
  await add.getByRole("button", { name: /^Add to Phoenix Assistant/ }).waitFor();
  await crop("17-add-to-skill-ok", folderRow("Project Phoenix 2026"), add);
  await add.getByRole("button", { name: /^Add to Phoenix Assistant/ }).click();
  await add.getByText(/Done — /).waitFor({ timeout: 15000 });
  await crop("18-add-to-skill-done", folderRow("Project Phoenix 2026"), add);
  await add.getByRole("button", { name: /Close|Cancel/ }).click();

  // Who can search, stop searching
  await folderMenu("Project Phoenix 2026");
  await page.getByRole("menuitem", { name: "Who can search…" }).click();
  await page.getByRole("dialog", { name: "Who can search this folder" }).waitFor();
  await crop("19-who-can-search", folderRow("Project Phoenix 2026"), page.getByRole("dialog", { name: "Who can search this folder" }));
  await page.getByRole("dialog", { name: "Who can search this folder" }).getByRole("button", { name: "Cancel" }).click();
  await folderMenu("Project Phoenix 2026");
  await page.getByRole("menuitem", { name: "Stop searching" }).click();
  await crop("20-stop-searching-confirm", folderRow("Project Phoenix 2026"), page.getByText(/Stop searching ‘/).first().locator(".."));
  await page.keyboard.press("Escape");

  // A skill shared with you: flagged up front, one button makes your copy
  await open("/chat/@colleague/contract-expert");
  await folderMenu("Market research");
  await page.getByRole("menuitem", { name: "Add to a skill…" }).click();
  const fork = page.getByRole("dialog", { name: "Add this folder to a skill" });
  await fork.getByText(/You can’t change Contract Expert/).waitFor({ timeout: 15000 });
  await page.waitForTimeout(1200); // the amber prompt grows as its text lays out
  await crop("21-fork-prompt", folderRow("Market research"), fork);
  await fork.getByRole("button", { name: /^Make my copy and add/ }).click();
  await fork.getByText(/your copy ‘Contract Expert \(Fork\)’/).waitFor({ timeout: 15000 });
  await crop("23-fork-done", folderRow("Market research"), fork);

  // A skill that already searches your own folders
  await open("/chat/@colleague/research-desk");
  await folderMenu("Market research");
  await page.getByRole("menuitem", { name: "Add to a skill…" }).click();
  await page.getByText(/Nothing to add: Research Desk/).waitFor({ timeout: 15000 });
  await crop("24-my-folders-covered", folderRow("Market research"), page.getByRole("dialog", { name: "Add this folder to a skill" }));
}
