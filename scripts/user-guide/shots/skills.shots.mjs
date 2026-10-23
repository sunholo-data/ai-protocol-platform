// Screenshots for docs/user-guide/skills.md. File names match the page's image references.
import { json } from "./lib.mjs";
import { PLATFORM } from "./fixtures.mjs";

// Real labels from the backend tool catalog (adk/tools.py TOOL_CATALOG + private tools), so the
// checklist in the screenshot reads exactly like the deployed Studio.
const TOOLS = [
  { name: "google_search", label: "Web search", description: "Look up current information on the public web via Google.", category: "Search" },
  { name: "url_processing", label: "Read a web page", description: "Fetch and read the content of a URL the user provides.", category: "Search" },
  {
    name: "knowledge_search",
    label: "Search knowledge bases",
    description: "Search the knowledge bases bound to this skill (optionally also each user's own searchable folders) and cite the passages found.",
    category: "Search",
  },
  { name: "list_documents", label: "List documents", description: "See the documents the user has uploaded to this workspace.", category: "Documents" },
  { name: "get_document_content", label: "Read a document", description: "Open and read the full text of an uploaded document.", category: "Documents" },
  { name: "generate_document", label: "Create a document", description: "Write a Word document into the workspace, optionally styled from a template.", category: "Documents" },
  { name: "code_execution", label: "Run code", description: "Execute code to do calculations, data crunching, or charts.", category: "Analysis" },
];

const MODELS = {
  models: [
    { id: "model-best", provider: "google", description: "Most capable model" },
    { id: "model-pro", provider: "google", description: "Strong reasoning at lower cost" },
    { id: "model-lite", provider: "google", description: "Fast and cheap" },
  ],
  tier_defaults: { smart: "model-best", pro: "model-pro", lite: "model-lite" },
};

/** Page-specific synthetic data: categories for the skill switcher, two more built-in skills,
 * and the Studio's catalogs. */
export function fixture(lib) {
  const cat = { "demo-researcher": "assistant", "phoenix-assistant": "specialist", "contract-expert": "specialist", "research-desk": "assistant" };
  for (const s of lib.skills) s.skillMetadata = { ...s.skillMetadata, category: cat[s.skillId] };
  lib.skills.push(
    {
      skillId: "web-researcher",
      slug: "web-researcher",
      name: "web-researcher",
      displayName: "Web Researcher",
      description: "Searches the web and cites what it finds.",
      ownerId: PLATFORM,
      accessControl: { type: "public" },
      managedBy: "template",
      canEdit: false,
      skillMetadata: { tools: ["google_search", "url_processing"], category: "tool" },
    },
    {
      skillId: "document-writer",
      slug: "document-writer",
      name: "document-writer",
      displayName: "Document Writer",
      description: "Drafts reports and letters as Word documents.",
      ownerId: PLATFORM,
      accessControl: { type: "public" },
      managedBy: "template",
      canEdit: false,
      skillMetadata: { tools: ["generate_document"], category: "specialist" },
    },
  );
  // A built-in skill opened in Studio is read-only for a non-admin: canEdit false.
  lib.skills.find((s) => s.skillId === "demo-researcher").canEdit = false;
  lib.kbs["kb-handbook"] = {
    kbId: "kb-handbook",
    name: "Team handbook",
    documentCount: 12,
    audience: "everyone at yourcompany.com",
    canManage: false,
    accessControl: { type: "domain", domain: "yourcompany.com" },
    lastSyncSummary: { state: "done", total: 12, done: 12, failed: 0 },
  };
  return async function handle(route) {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy", "");
    const m = route.request().method();
    if (p === "/api/tools") return json(route, 200, { tools: TOOLS }), true;
    if (p === "/api/models") return json(route, 200, MODELS), true;
    if (p === "/api/voice/voices") return json(route, 200, { languages: [], voices: {} }), true;
    if (p === "/api/skills" && m === "POST") {
      const body = route.request().postDataJSON() ?? {};
      const created = { ...body, skillId: "new-skill", slug: body.name, ownerId: "guide-user", canEdit: true };
      lib.skills.push(created);
      return json(route, 201, created), true;
    }
    return false;
  };
}

export default async function skills({ page, base, shot, tidy }) {
  /** Crop to the union of `targets` (plus padding), clamped to the page. */
  async function crop(file, targets, { pad = 16, minWidth = 0 } = {}) {
    await page.waitForTimeout(500);
    const boxes = (await Promise.all(targets.map((t) => t.boundingBox()))).filter(Boolean);
    const vp = page.viewportSize();
    const x = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
    const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
    const right = Math.min(vp.width, Math.max(x + minWidth, ...boxes.map((b) => b.x + b.width + pad)));
    const bottom = Math.min(vp.height, Math.max(...boxes.map((b) => b.y + b.height)) + pad);
    await shot(file, "page", { wait: 0, clip: { x, y, width: right - x, height: bottom - y } });
  }
  const go = async (url) => {
    await page.goto(`${base}${url}`, { waitUntil: "networkidle", timeout: 90000 });
    await tidy();
    await page.waitForTimeout(800);
  };

  // The skill switcher
  await page.setViewportSize({ width: 1280, height: 900 });
  await go("/chat/@aitana-platform/demo-researcher");
  const switcher = page.getByRole("button", { name: "Switch skill" }).first();
  await switcher.click();
  const search = page.getByRole("textbox", { name: "Search skills" });
  await search.waitFor();
  const list = search.locator("xpath=ancestor::div[contains(@class,'rounded') or contains(@class,'border')][1]");
  await crop("01-skill-switcher", [switcher, list], { pad: 10 });
  await page.keyboard.press("Escape");

  // Header buttons: configure this skill, create a new skill
  await crop("02-header-buttons", [
    page.getByRole("button", { name: "Configure this skill" }).first(),
    page.getByRole("button", { name: "Create a new skill" }).first(),
  ], { pad: 12 });

  // A built-in skill in Studio: read-only, Fork to customize
  await page.setViewportSize({ width: 1280, height: 1300 });
  await go("/skills/studio/demo-researcher");
  await page.getByText("This is a built-in").first().waitFor({ timeout: 30000 });
  await crop("03-built-in-skill", [page.locator("header").first(), page.getByText("This is a built-in").first()], { pad: 8 });
  await page.getByRole("button", { name: /^Fork/ }).first().click();
  await page.getByText("Demo Researcher (Fork)").first().waitFor({ timeout: 30000 });
  await crop("04-your-copy", [page.locator("header").first(), page.getByText("Display name").first()], { pad: 8 });

  // A colleague's skill shared with you: read-only up front, Fork offered, Save off
  await go("/skills/studio/contract-expert");
  const sharedNote = page.getByText("This skill was shared with you").first();
  await sharedNote.waitFor({ timeout: 30000 });
  if (!(await page.getByRole("button", { name: "Save" }).first().isDisabled())) throw new Error("Save should be disabled on a shared skill");
  await crop("09-shared-skill", [page.locator("header").first(), sharedNote], { pad: 8 });

  // A new skill
  await page.setViewportSize({ width: 1280, height: 860 });
  await go("/skills/studio/new");
  await page.getByPlaceholder("my-new-skill").fill("meeting-notes");
  const field = (label) => page.locator("label").filter({ hasText: label }).first().locator("input, textarea, select").first();
  await field("Display name").fill("Meeting Notes");
  await field("Description").fill("Turns a meeting transcript into notes, decisions and actions.");
  await page.getByPlaceholder(/Help users review contracts/).fill("Turn a meeting transcript into short notes, the decisions made, and a list of actions with owners.");
  await shot("05-new-skill");

  // Instructions, model, tools, knowledge bases, who can use it
  await page.setViewportSize({ width: 1280, height: 2400 });
  await page.waitForTimeout(600);
  const fieldset = (legend) => page.locator("fieldset").filter({ has: page.locator("legend", { hasText: legend }) }).first();
  await page.getByPlaceholder(/Be precise and always cite/).fill("Keep it short. Use the attendees' names exactly as they appear.");
  await page.getByPlaceholder(/Never invent figures/).fill("Never invent an action or an owner that isn't in the transcript.");
  await crop("06-instructions", [fieldset("Instructions")], { pad: 8 });

  const model = page.locator("label").filter({ hasText: "Language model" }).first();
  await crop("07-model-and-tools", [model, fieldset("Tools")], { pad: 8 });

  await fieldset("Tools").getByText("Search knowledge bases", { exact: true }).click();
  const kbBox = fieldset("Knowledge bases this skill searches");
  await kbBox.waitFor({ timeout: 15000 });
  await kbBox.getByText("Each user’s own searchable folders").click();
  await kbBox.getByText("Team handbook").click();
  await page.waitForTimeout(800);
  await crop("08-studio-knowledge-bases", [kbBox], { pad: 8 });

}
