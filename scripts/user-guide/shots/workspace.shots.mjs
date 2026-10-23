// Screenshots for docs/user-guide/workspace.md. File names match the page's image references.
//
// The chat turn is a SYNTHETIC AG-UI stream (the same event shapes a real
// web-search turn produces — captured once from a deployed env and replaced
// with invented content): a web_search_agent tool call, a Sources result
// (kind "sources", surface "web_sources"), and the answer text.
import { json } from "./lib.mjs";

const QUESTION = "What's a typical payback period for rooftop solar on an office building?";
const ANSWER =
  "For a commercial rooftop system the payback period is usually **6 to 9 years**, depending on " +
  "electricity prices, how much of the power the building uses itself, and any grants. " +
  "Your own *Phoenix project brief* assumes 7 years for the pilot site.";

const SOURCES = [
  { title: "solar-guide.example.org", uri: "https://solar-guide.example.org/commercial-payback", kind: "web", bucket: "", object: "", filename: "", snippet: "" },
  { title: "energy-facts.example.com", uri: "https://energy-facts.example.com/rooftop-economics", kind: "web", bucket: "", object: "", filename: "", snippet: "" },
  { title: "Phoenix project brief.docx", uri: "", kind: "gcs", bucket: "example-library", object: "projects/Phoenix project brief.docx", filename: "Phoenix project brief.docx", snippet: "" },
];

function sourcesSurface() {
  const id = "web_sources";
  const items = SOURCES.map((s, i) => ({ id: `src-${i + 2}`, component: "Text", text: s.title }));
  const components = [
    { id: "src-1", component: "Text", text: "Sources", variant: "h4" },
    ...items,
    { id: "src-list", component: "List", children: items.map((c) => c.id) },
    { id: "src-col", component: "Column", children: ["src-1", "src-list"] },
    { id: "src-card", component: "Card", child: "src-col" },
    { id: "root", component: "Column", children: ["src-card"] },
  ];
  return {
    surfaceId: id,
    sourceId: "guide-turn:web_search_agent:1",
    artifact: { kind: "sources", title: "Sources", description: "Web pages cited in this answer" },
    messages: [
      { version: "v0.9", createSurface: { surfaceId: id, catalogId: "https://a2ui.org/specification/v0_9/basic_catalog.json" } },
      { version: "v0.9", updateComponents: { surfaceId: id, components } },
      { version: "v0.9", updateDataModel: { surfaceId: id, value: { sources: SOURCES } } },
    ],
  };
}

function stream(threadId, runId) {
  const msg = "msg-answer";
  const tool = "call-search";
  const events = [
    { type: "RUN_STARTED", threadId, runId },
    { type: "TOOL_CALL_START", toolCallId: tool, toolCallName: "web_search_agent" },
    { type: "TOOL_CALL_ARGS", toolCallId: tool, delta: JSON.stringify({ request: "rooftop solar payback period commercial building" }) },
    { type: "TOOL_CALL_END", toolCallId: tool },
    { type: "TOOL_CALL_RESULT", messageId: "msg-tool", toolCallId: tool, content: "Payback for commercial rooftop solar is typically 6–9 years.", role: "tool" },
    { type: "CUSTOM", name: "A2UI_SURFACE", value: sourcesSurface() },
    { type: "TEXT_MESSAGE_START", messageId: msg, role: "assistant" },
    { type: "TEXT_MESSAGE_CONTENT", messageId: msg, delta: ANSWER },
    { type: "TEXT_MESSAGE_END", messageId: msg },
    { type: "RUN_FINISHED", threadId, runId },
  ];
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

/** The chat stream, plus opening the library source in the Document tab. */
export function fixture(lib) {
  return async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace("/api/proxy", "");
    if (/^\/api\/skill\/[^/]+\/stream$/.test(p) && req.method() === "POST") {
      const body = req.postDataJSON() ?? {};
      await new Promise((r) => setTimeout(r, 600));
      await route.fulfill({ status: 200, contentType: "text/event-stream", body: stream(body.threadId ?? "t", body.runId ?? "r") });
      return true;
    }
    // This chat's session: it exists and has no earlier messages (a 404 makes the
    // app start a new session on every check).
    let mm;
    if ((mm = p.match(/^\/api\/sessions\/([^/]+)(\/messages)?$/)) && req.method() === "GET") {
      await json(route, 200, mm[2] ? { messages: [] } : { sessionId: mm[1], skillId: "demo-researcher", title: "", messages: [] });
      return true;
    }
    if (p === "/api/voice/config") {
      await json(route, 200, { enabled: false });
      return true;
    }
    if (p === "/api/documents/d1" && req.method() === "GET") {
      await json(route, 200, {
        id: "d1",
        originalFilename: "Phoenix project brief.docx",
        sourceFormat: "docx",
        parseStatus: "parsed",
        folderId: "f-phoenix",
        blocks: [
          { type: "heading", level: 1, text: "Project Phoenix — pilot brief" },
          { type: "text", text: "Project Phoenix fits rooftop solar to three office buildings, starting with a 120 kW pilot on the north site." },
          { type: "heading", level: 2, text: "Assumptions" },
          {
            type: "list",
            items: ["Payback period: 7 years", "On-site use of generated power: 80%", "Installation in Q3, commissioning in Q4"],
          },
          { type: "heading", level: 2, text: "Next steps" },
          { type: "text", text: "Confirm the roof survey, then request three supplier quotes." },
        ],
      });
      return true;
    }
    if (p === "/api/documents/d1/sessions") {
      await json(route, 200, { sessions: [] });
      return true;
    }
    if (p === "/api/documents/import-by-reference" && req.method() === "POST") {
      await json(route, 200, { id: "d1", docId: "d1", originalFilename: "Phoenix project brief.docx", parseStatus: "parsed", folderId: "f-phoenix" });
      return true;
    }
    return false;
  };
}

export default async function workspace({ page, base, shot, tidy }) {
  await page.goto(`${base}/chat/@aitana-platform/demo-researcher`, { waitUntil: "networkidle", timeout: 90000 });
  await tidy();
  const box = page.getByPlaceholder(/^Message /).first();
  await box.waitFor({ timeout: 30000 });
  await page.getByText("Connecting").first().waitFor({ state: "detached", timeout: 30000 }).catch(() => {});
  await box.fill(QUESTION);
  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText(/6 to 9 years/).first().waitFor({ timeout: 30000 });
  await page.getByRole("heading", { name: "Sources" }).first().waitFor({ timeout: 15000 });
  await shot("01-answer-with-sources", "page", { wait: 1200 });

  const tab = (name) => page.getByRole("tab", { name: new RegExp(name) }).first();
  // The workbench pane only: from its "Workbench" title to the right edge.
  const pane = async (height = 470) => {
    const t = await page.getByText("Workbench", { exact: true }).first().boundingBox();
    const vw = page.viewportSize().width;
    return { x: t.x - 18, y: t.y - 22, width: vw - (t.x - 18), height };
  };

  await shot("05-sources-tab", "page", { wait: 400, clip: await pane(330) });

  await tab("Workspace").click();
  await shot("02-workspace-home", "page", { wait: 600, clip: await pane(300) });

  await tab("Activity").click();
  await shot("03-activity", "page", { wait: 600, clip: await pane(260) });

  await tab("Sources").click();
  await page.getByRole("button", { name: /Open Phoenix project brief\.docx in the Document tab/ }).click();
  await tab("Document").waitFor();
  await page.waitForTimeout(1500);
  // The Styled view renders the real file; the synthetic document has only its
  // parsed text, so photograph the Text view.
  await page.getByRole("button", { name: "Text", exact: true }).or(page.getByRole("tab", { name: "Text", exact: true })).first().click();
  await page.getByText("Project Phoenix — pilot brief").first().waitFor({ timeout: 15000 });
  await shot("04-document-tab", "page", { wait: 800, clip: await pane(500) });

  // Collapse it: a thin strip stays on the right edge to bring it back.
  await page.getByRole("button", { name: "Collapse workbench" }).click();
  await page.getByRole("button", { name: "Expand workbench" }).waitFor();
  await shot("06-collapsed", "page", { wait: 600 });
}
