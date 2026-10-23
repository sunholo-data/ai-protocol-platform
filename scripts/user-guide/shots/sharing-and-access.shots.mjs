// Screenshots for docs/user-guide/sharing-and-access.md. File names match the page's image references.
import { json } from "./lib.mjs";

/** Studio needs the tool catalog, models and voices; synthetic and minimal. */
export function fixture() {
  return async (route) => {
    const p = new URL(route.request().url()).pathname.replace("/api/proxy", "");
    if (p === "/api/tools") return json(route, 200, { tools: [] }), true;
    if (p === "/api/models") return json(route, 200, { models: [], tier_defaults: {} }), true;
    if (p === "/api/voice/voices") return json(route, 200, { languages: [], voices: {} }), true;
    return false;
  };
}

export default async function sharing({ page, base, tidy }) {
  await page.setViewportSize({ width: 1280, height: 1500 });
  const out = new URL("../../../docs/user-guide/img/sharing-and-access/", import.meta.url).pathname;
  /** Crop to the union of `targets` with a margin, so focus rings aren't cut. */
  const shot = async (file, ...targets) => {
    await page.waitForTimeout(400);
    await targets[0].scrollIntoViewIfNeeded();
    const boxes = (await Promise.all(targets.map((t) => t.boundingBox()))).filter(Boolean);
    const pad = 16;
    const x = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
    const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
    const right = Math.max(...boxes.map((b) => b.x + b.width)) + pad;
    const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
    await page.screenshot({ path: `${out}${file}.png`, clip: { x, y, width: right - x, height: bottom - y } });
    console.log(`  shot sharing-and-access/${file}.png`);
  };
  const go = async (url) => {
    await page.goto(`${base}${url}`, { waitUntil: "networkidle", timeout: 90000 });
    await tidy();
    await page.waitForTimeout(800);
  };

  // Skill Studio: who can use this skill
  await go("/skills/studio/phoenix-assistant");
  const editor = page.getByText("Who can use this skill", { exact: true }).locator("xpath=ancestor::div[contains(@class,'space-y-2')][1]");
  const select = editor.getByRole("combobox");
  await select.selectOption("domain");
  await editor.getByPlaceholder("energy.example").fill("yourcompany.com");
  await editor.scrollIntoViewIfNeeded();
  await shot("01-who-can-use-domain", editor);
  await select.selectOption("tagged");
  await editor.getByRole("textbox").fill("finance-team, leadership");
  await shot("02-who-can-use-group", editor);

  // The skill picker groups what you can open
  await go("/chat/@aitana-platform/demo-researcher");
  await page.getByRole("button", { name: "Switch skill" }).click();
  const search = page.getByLabel("Search skills");
  await search.waitFor({ timeout: 15000 });
  // The dropdown: the search box's container (a floating Radix panel).
  const panel = search.locator("xpath=..");
  await shot("03-skill-picker", panel);
  await page.keyboard.press("Escape");

  // A link to a skill you can't open
  await go("/chat/@colleague/board-reports");
  await page.getByText("Skill not found").waitFor({ timeout: 30000 });
  await page.getByText("Signed in as").waitFor();
  const notFound = page.locator("main").filter({ hasText: "Skill not found" });
  await shot("04-skill-not-found", notFound.locator("xpath=./*").first(), notFound.getByRole("link").last());

  // An account from an organisation that isn't set up
  // The deployment refuses the account on every call, as the backend does for an unmapped domain.
  await page.route("**/api/proxy/api/**", (r) =>
    json(r, 403, { detail: { code: "DOMAIN_NOT_PERMITTED", message: "" } }),
  );
  await go("/chat/@aitana-platform/demo-researcher");
  await page.getByText("Access restricted").waitFor({ timeout: 30000 });
  const restricted = page.locator("main").filter({ hasText: "Access restricted" });
  await shot("05-access-restricted", restricted.locator("xpath=./*").first(), restricted.getByRole("button").last());
}
