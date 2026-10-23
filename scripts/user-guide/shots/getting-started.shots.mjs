// Screenshots for docs/user-guide/getting-started.md. File names match the page's image references.
import { json } from "./lib.mjs";
import { UID } from "./fixtures.mjs";

const NOW = Date.now();
const ago = (mins) => new Date(NOW - mins * 60000).toISOString();

const SESSIONS = [
  ["s-kickoff", "Summarise the Phoenix kick-off notes", "demo-researcher", "Demo Researcher", 12, 4],
  ["s-trends", "Industry trends for 2026", "demo-researcher", "Demo Researcher", 180, 6],
  ["s-budget", "Questions about the Phoenix budget", "phoenix-assistant", "Phoenix Assistant", 60 * 26, 2],
].map(([id, title, skill, label, mins, turns]) => ({
  session_id: id,
  document_ids: [],
  skill_id: skill,
  owner_uid: UID,
  title,
  turn_count: turns,
  first_message_at: ago(mins + 5),
  last_message_at: ago(mins),
  archived_at: null,
  is_owner: true,
  skill_label: label,
}));

const MESSAGES = [
  { role: "user", content: "Summarise the Phoenix kick-off notes in three bullet points.", timestamp: NOW - 13 * 60000 },
  {
    role: "assistant",
    timestamp: NOW - 12 * 60000,
    content:
      "Here are the main points from **Kick-off meeting notes.docx**:\n\n" +
      "- **Scope:** Phase 1 covers the pilot site only; Phase 2 is planned for Q3.\n" +
      "- **Budget:** the team agreed a ceiling of 1.2M, reviewed monthly.\n" +
      "- **Risks:** supplier lead times are the top risk; an owner is named for each.\n\n" +
      "Source: Kick-off meeting notes.docx, section 2.",
  },
];

// The user guide skill, answering from the guide: a knowledge_search call whose
// hits are all guide pages, so the result is a Help tab (kind "help") that
// carries page REFERENCES; the tab renders the page from the app's own build.
// Same event shapes as a real turn (private/knowledge/tools.py).
const GUIDE_SKILL = {
  skillId: "platform-guide",
  slug: "platform-guide",
  name: "platform-guide",
  displayName: "User guide",
  description: "Answers questions about using the app, from the user guide.",
  ownerId: "aitana-platform",
  accessControl: { type: "domain", domain: "yourcompany.com" },
  managedBy: null,
  canEdit: false,
  skillMetadata: { tools: ["knowledge_search"] },
};
const HELP_QUESTION = "How do I upload a whole folder of documents?";
const HELP_ANSWER =
  "Open the **Your files** panel, then use **Upload folder** and pick the folder on your computer. " +
  "Everything inside it is uploaded into a new folder with the same name, and a summary shows what was skipped.\n\n" +
  "See [Upload a whole folder](/help/folders#upload-a-whole-folder). To add single files instead, see " +
  "[Upload a document](/help/documents#upload-a-document).";
const HELP_REFS = [
  { slug: "folders", anchor: "upload-a-whole-folder", title: "Folders › Upload a whole folder" },
  { slug: "folders", anchor: "read-the-summary", title: "Folders › Read the summary" },
  { slug: "documents", anchor: "upload-a-document", title: "Documents › Upload a document" },
];

function helpStream(threadId, runId) {
  const id = "knowledge_sources";
  const sources = HELP_REFS.map((r) => ({ title: `${r.title} — User guide`, uri: `/help/${r.slug}#${r.anchor}`, snippet: "" }));
  const events = [
    { type: "RUN_STARTED", threadId, runId },
    { type: "TOOL_CALL_START", toolCallId: "call-kb", toolCallName: "knowledge_search" },
    { type: "TOOL_CALL_ARGS", toolCallId: "call-kb", delta: JSON.stringify({ query: "upload a whole folder" }) },
    { type: "TOOL_CALL_END", toolCallId: "call-kb" },
    { type: "TOOL_CALL_RESULT", messageId: "msg-tool", toolCallId: "call-kb", content: JSON.stringify({ status: "ok", results: [] }), role: "tool" },
    {
      type: "CUSTOM",
      name: "A2UI_SURFACE",
      value: {
        surfaceId: id,
        sourceId: "guide-turn:knowledge_search:1",
        artifact: { kind: "help", title: "Help", description: HELP_REFS[0].title },
        messages: [
          { version: "v0.9", createSurface: { surfaceId: id, catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json" } },
          { version: "v0.9", updateComponents: { surfaceId: id, components: [{ id: "root", component: "Column", children: [] }] } },
          { version: "v0.9", updateDataModel: { surfaceId: id, value: { sources, help: HELP_REFS } } },
        ],
      },
    },
    { type: "TEXT_MESSAGE_START", messageId: "msg-answer", role: "assistant" },
    { type: "TEXT_MESSAGE_CONTENT", messageId: "msg-answer", delta: HELP_ANSWER },
    { type: "TEXT_MESSAGE_END", messageId: "msg-answer" },
    { type: "RUN_FINISHED", threadId, runId },
  ];
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

// The Stop button only shows while a reply is in flight: hold the chat
// request open until the shot is taken, then answer it.
let releaseReply = () => {};
const replyHeld = new Promise((r) => {
  releaseReply = r;
});

function shortStream(threadId, runId) {
  const events = [
    { type: "RUN_STARTED", threadId, runId },
    { type: "TEXT_MESSAGE_START", messageId: "msg-risks", role: "assistant" },
    { type: "TEXT_MESSAGE_CONTENT", messageId: "msg-risks", delta: "The main risks are supplier lead times and the pilot site's grid connection." },
    { type: "TEXT_MESSAGE_END", messageId: "msg-risks" },
    { type: "RUN_FINISHED", threadId, runId },
  ];
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

export function fixture() {
  return async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace("/api/proxy", "");
    if (p === "/api/skill/demo-researcher/stream" && route.request().method() === "POST") {
      const body = route.request().postDataJSON() ?? {};
      await replyHeld;
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: shortStream(body.threadId ?? "t", body.runId ?? "r") });
      return true;
    }
    if (/^\/api\/skills\/by-slug\/@?[^/]+\/platform-guide$/.test(p) || p === "/api/skills/platform-guide") {
      return json(route, 200, GUIDE_SKILL), true;
    }
    if (p === "/api/skill/platform-guide/stream" && route.request().method() === "POST") {
      const body = route.request().postDataJSON() ?? {};
      await new Promise((r) => setTimeout(r, 400));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: helpStream(body.threadId ?? "t", body.runId ?? "r") });
      return true;
    }
    if (p === "/api/sessions" && route.request().method() === "GET") {
      // ?shared=true is "Shared with me" (others' threads): none for this user.
      const shared = url.searchParams.get("shared") === "true";
      return json(route, 200, { sessions: shared ? [] : SESSIONS, next_cursor: null }), true;
    }
    if (p === "/api/sessions/recent") return json(route, 200, { sessions: [] }), true;
    if (p === "/api/sessions/s-kickoff") return json(route, 200, SESSIONS[0]), true;
    if (p === "/api/voice/config") return json(route, 200, { enabled: false }), true;
    if (p === "/api/sessions/s-kickoff/messages") {
      return json(route, 200, { messages: MESSAGES, a2ui_surfaces: [] }), true;
    }
    return false;
  };
}

export default async function gettingStarted({ page, base, shot, tidy, around }) {
  const open = async (url) => {
    await page.goto(`${base}${url}`, { waitUntil: "networkidle", timeout: 90000 });
    await tidy();
    const show = page.getByRole("button", { name: "Show sidebar" });
    if (await show.count()) await show.first().click();
    await page.waitForTimeout(1000);
  };
  const header = () => page.locator("header").first();
  const sidebar = () => page.locator("aside").filter({ hasText: "Your files" }).first();

  // The chat page: header, sidebar, conversation area
  await open("/chat/@aitana-platform/demo-researcher");
  await page.getByText("Summarise the Phoenix kick-off notes").first().waitFor({ timeout: 30000 });
  await shot("01-chat-page");

  // Pick a skill
  await page.getByRole("button", { name: "Switch skill" }).click();
  await page.getByPlaceholder("Search skills…").waitFor();
  const popover = page.locator("[data-radix-popper-content-wrapper]").first();
  await page.waitForTimeout(500);
  const hb = await header().boundingBox();
  const pb = await popover.boundingBox();
  await shot("02-skill-switcher", "page", { clip: { x: 0, y: 0, width: Math.min(1280, pb.x + pb.width + 24), height: pb.y + pb.height + 16 } });
  await page.keyboard.press("Escape");
  await page.locator("main").first().click({ position: { x: 600, y: 600 } }).catch(() => {});
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.mouse.move(1200, 800);

  // New chat + the message box
  await shot("03-header", "page", { clip: { x: 0, y: 0, width: 1280, height: hb.height } });
  const box = page.getByRole("textbox").filter({ has: page.locator("xpath=self::textarea") }).first();
  const composer = page.locator("form").filter({ has: page.locator("textarea") }).last();
  await page.locator("textarea").last().fill("What are the main risks in the Phoenix project?");
  void box;
  const cb = await composer.boundingBox();
  await shot("04-message-box", "page", { clip: { x: cb.x - 16, y: cb.y - 16, width: cb.width + 32, height: cb.height + 32 } });

  await page.locator("textarea").last().fill("");

  // Past conversations
  await page.setViewportSize({ width: 1280, height: 1500 });
  const crop = around(sidebar);
  await crop("05-past-conversations", page.getByText("Past conversations", { exact: true }), page.getByText("Questions about the Phoenix budget").first());

  // An answered conversation, reopened
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.getByText("Summarise the Phoenix kick-off notes").first().click();
  await page.getByText(/Here are the main points/).first().waitFor({ timeout: 30000 });
  await page.waitForTimeout(800);
  await shot("06-conversation");

  // Help: the account menu, then the guide
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Help" }).waitFor();
  const mb = await page.getByRole("menuitem", { name: "Help" }).locator("xpath=ancestor::*[@role='menu' or contains(@class,'absolute')][1]").boundingBox();
  const x0 = Math.max(0, mb.x - 2);
  await shot("07-account-menu-help", "page", { clip: { x: x0, y: 0, width: 1280 - x0, height: mb.y + mb.height + 16 } });
  await page.keyboard.press("Escape");
  await page.goto(`${base}/help`, { waitUntil: "networkidle" });
  await tidy();
  await page.getByText(/Ask about/).first().waitFor({ timeout: 30000 });
  await shot("08-help-index", "page", { clip: { x: 0, y: 0, width: 1280, height: 520 } });

  // Ask the user guide skill: the answer links to the guide, and the Help tab
  // shows the page it came from, opened at the section.
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.goto(`${base}/chat/@aitana-platform/platform-guide`, { waitUntil: "networkidle", timeout: 90000 });
  await tidy();
  const askBox = page.getByPlaceholder(/^Message /).first();
  await askBox.waitFor({ timeout: 30000 });
  await page.getByText("Connecting").first().waitFor({ state: "detached", timeout: 30000 }).catch(() => {});
  await askBox.fill(HELP_QUESTION);
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText(/Everything inside it is uploaded/).first().waitFor({ timeout: 30000 });
  const helpHeading = page.getByRole("heading", { name: "Upload a whole folder" }).first();
  await helpHeading.waitFor({ timeout: 30000 });
  await page.getByRole("img").filter({ hasNot: page.locator("svg") }).first().waitFor({ timeout: 15000 }).catch(() => {});
  await shot("09-ask-for-help", "page", { wait: 1500 });

  // A help link in the answer opens its page in the same tab.
  await page.getByRole("link", { name: "Upload a document" }).first().click();
  await page.getByRole("heading", { name: "Upload a document" }).first().waitFor({ timeout: 15000 });
  if (!page.url().includes("/chat/")) throw new Error(`help link navigated away: ${page.url()}`);

  // Sent: while the reply is on its way the arrow is a square Stop button.
  await page.setViewportSize({ width: 1280, height: 860 });
  await page.goto(`${base}/chat/@aitana-platform/demo-researcher`, { waitUntil: "networkidle", timeout: 90000 });
  await tidy();
  const composerEnd = page.locator("form").filter({ has: page.locator("textarea") }).last();
  await page.getByText("Connecting").first().waitFor({ state: "detached", timeout: 30000 }).catch(() => {});
  await page.locator("textarea").last().fill("What are the main risks in the Phoenix project?");
  await page.keyboard.press("Enter");
  const stop = page.getByRole("button", { name: /^Stop/ }).first();
  await stop.waitFor({ timeout: 15000 });
  const sb = await composerEnd.boundingBox();
  await shot("10-stop-button", "page", { wait: 300, clip: { x: sb.x - 16, y: sb.y - 16, width: sb.width + 32, height: sb.height + 32 } });
  releaseReply();
  await page.getByText(/supplier lead times and the pilot site/).first().waitFor({ timeout: 15000 });
}
