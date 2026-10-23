/**
 * Shared harness for user-guide screenshots (make guide-shots).
 *
 * SECURITY: screenshots ship in the public template, so they must never show
 * real data. The harness therefore drives the LOCAL frontend only, and answers
 * every /api/proxy call from a synthetic fixture (fixtures.mjs) — a call the
 * fixture doesn't handle is refused, never passed through to a backend. There
 * is no login and no deployed URL anywhere in this directory.
 */
import { chromium } from "playwright-core";

export const json = (route, status, body) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** Open the app with the API mocked by `handle(route) -> handled?`. */
export async function open({ base, handle, viewport = { width: 1280, height: 860 } }) {
  if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(base)) {
    throw new Error(`refusing to screenshot ${base}: the harness only drives a local frontend`);
  }
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const unhandled = new Set();
  await page.route("**/api/proxy/**", async (route) => {
    if (await handle(route)) return;
    const u = new URL(route.request().url());
    unhandled.add(`${route.request().method()} ${u.pathname}`);
    // Never fall through to a real backend: an unmocked call gets an empty answer.
    return json(route, 404, { detail: "not in the screenshot fixture" });
  });
  page.on("pageerror", (e) => console.error("pageerror", e.message));
  return { browser, page, unhandled };
}

/** Hide dev-only chrome so a shot looks like the deployed app. */
export async function tidy(page) {
  await page.addStyleTag({
    // Dev-only chrome off; the sidebar's file list un-capped (max-h-[45vh]) so a
    // menu or panel inside it is photographed whole rather than scrolled off.
    content:
      "nextjs-portal{display:none!important} .bg-yellow-100.border-b.border-yellow-300{display:none!important} aside .max-h-\\[45vh\\],aside .max-h-\\[30vh\\]{max-height:none!important}",
  });
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("body *")) {
      if (el.children.length === 0 && /LOCAL_MODE/.test(el.textContent || "")) {
        let top = el;
        while (top.parentElement && top.parentElement !== document.body && top.parentElement.children.length === 1) top = top.parentElement;
        top.remove();
        break;
      }
    }
  });
}

/** `shot(file, target)` writes <out>/<file>.png from the page, a locator, or the sidebar. */
export function shooter(page, out) {
  return async function shot(file, target = "page", { wait = 400, clip } = {}) {
    await page.waitForTimeout(wait);
    const path = `${out}/${file}.png`;
    if (target === "page") await page.screenshot({ path, clip });
    else await target.screenshot({ path });
    console.log("  shot", path.split("/").slice(-2).join("/"));
  };
}

/**
 * `shotAround(file, ...locators)` crops to the column the first locator sits in
 * (e.g. the sidebar), from the top of the highest element to the bottom of the
 * lowest, plus padding — the tight, readable crops the guide uses. Elements are
 * scrolled into view first.
 */
export function aroundShooter(page, out, column) {
  return async function shotAround(file, ...targets) {
    await page.waitForTimeout(400);
    await targets[0].scrollIntoViewIfNeeded();
    const col = await column().boundingBox();
    const boxes = (await Promise.all(targets.map((t) => t.boundingBox()))).filter(Boolean);
    const pad = 12;
    const top = Math.max(col.y, Math.min(...boxes.map((b) => b.y)) - pad);
    const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
    const path = `${out}/${file}.png`;
    await page.screenshot({ path, clip: { x: col.x, y: top, width: col.width, height: Math.max(40, bottom - top) } });
    console.log("  shot", path.split("/").slice(-2).join("/"));
  };
}
