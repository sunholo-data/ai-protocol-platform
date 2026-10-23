#!/usr/bin/env node
/**
 * Check the user guide pages:   node scripts/user-guide/check.mjs   (make guide-check)
 *
 * Fails on what would ship a wrong or leaky page; warns on what only ages it.
 *   ERROR  the bundle doesn't build (missing image, unknown overlay anchor, no title)
 *   ERROR  an image with empty alt text, or no caption
 *   ERROR  a bold UI label that doesn't exist in frontend/src (a renamed button)
 *   ERROR  a platform page naming a customer: any `names:` a fork's
 *          docs/customers/<c>/user-guide/guide.yaml declares
 *   ERROR  audience not user|owner|admin
 *   WARN   `reviewed` missing or older than 6 months
 *
 * Bold spans that end in . : ! or ? are emphasis, not labels (a label never
 * ends a sentence), so `**Your copy is a snapshot.**` is not looked up.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { GuideError, build, parseFrontMatter } from "./bundle.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const AUDIENCES = new Set(["user", "owner", "admin"]);
const STALE_DAYS = 183;

/** Every string a user can see in the frontend, normalised for lookup. */
export function uiCorpus(srcDir = path.join(ROOT, "frontend/src")) {
  const parts = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name !== "__tests__" && e.name !== "node_modules") walk(p);
      } else if (/\.(tsx?|jsx?)$/.test(e.name) && !/\.test\./.test(e.name)) {
        parts.push(fs.readFileSync(p, "utf8"));
      }
    }
  };
  if (fs.existsSync(srcDir)) walk(srcDir);
  parts.push(...backendLabels());
  return normalise(parts.join("\n"));
}

/** Labels the UI shows but the backend defines — tool catalog entries (`"label": "Search knowledge bases"`). */
function backendLabels(dir = path.join(ROOT, "backend")) {
  const out = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) {
        if (![".venv", "tests", "__pycache__", "bundled", "node_modules"].includes(e.name)) walk(p);
      } else if (e.name.endsWith(".py")) {
        for (const m of fs.readFileSync(p, "utf8").matchAll(/["'](?:label|display_name|displayName)["']\s*[:=]\s*["']([^"']+)["']/g)) out.push(m[1]);
      }
    }
  };
  if (fs.existsSync(dir)) walk(dir);
  return out;
}

function normalise(s) {
  return s
    .replace(/&rsquo;|&#39;|&apos;|\\u2019/g, "’")
    .replace(/&lsquo;|\\u2018/g, "‘")
    .replace(/&hellip;|\\u2026/g, "…")
    .replace(/&amp;/g, "&")
    .replace(/\{"\s*"\}/g, " ")
    .replace(/[’']/g, "'")
    .replace(/\s+/g, " ");
}

/** The parts of a label that must appear literally: split at placeholders (… and quoted names). */
export function labelFragments(label) {
  return normalise(label)
    .split(/…|\.\.\.|‘[^’']*[’']|'[^']*'|\(Fork\)|\d+(?:\/\d+)?/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 2 && /[\p{L}\p{N}]/u.test(s));
}

export function boldLabels(body) {
  const out = [];
  let fenced = false;
  for (const line of body.split("\n")) {
    if (/^```/.test(line)) fenced = !fenced;
    if (fenced) continue;
    for (const m of line.replace(/`[^`]*`/g, "").matchAll(/\*\*([^*]+)\*\*/g)) {
      const label = m[1].trim();
      if (/[.:!?]$/.test(label)) continue; // emphasis, not a label
      out.push(label);
    }
  }
  return out;
}

export function checkPages({ platformDir, customersDir, corpus, today = new Date() } = {}) {
  const errors = [];
  const warnings = [];
  let guide;
  try {
    guide = build({ platformDir, customersDir });
  } catch (err) {
    if (err instanceof GuideError) return { errors: [err.message], warnings };
    throw err;
  }
  const names = new Set();
  const custDir = customersDir ?? path.join(ROOT, "docs/customers");
  if (fs.existsSync(custDir)) {
    for (const c of fs.readdirSync(custDir)) {
      const f = path.join(custDir, c, "user-guide", "guide.yaml");
      if (!fs.existsSync(f)) continue;
      const { meta } = parseFrontMatter(`---\n${fs.readFileSync(f, "utf8").trim()}\n---\n`, f);
      (Array.isArray(meta.names) ? meta.names : []).forEach((n) => names.add(n));
    }
  }
  const text = corpus ?? uiCorpus();
  const platformRoot = platformDir ?? path.join(ROOT, "docs/user-guide");
  for (const page of guide.manifest.pages) {
    const body = guide.pages.get(page.slug) ?? "";
    const where = `${page.slug}.md`;
    if (!AUDIENCES.has(page.audience)) errors.push(`${where}: audience ${JSON.stringify(page.audience)} is not user|owner|admin`);
    if (!page.summary) errors.push(`${where}: front matter needs a summary (the index card and search snippet)`);
    for (const m of body.matchAll(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g)) {
      if (!m[1].trim()) errors.push(`${where}: image ${m[2]} has no alt text`);
      if (!m[3]?.trim()) errors.push(`${where}: image ${m[2]} has no caption (the "title" after the path)`);
    }
    for (const label of boldLabels(body)) {
      const missing = labelFragments(label).filter((frag) => !text.includes(frag));
      if (missing.length) errors.push(`${where}: **${label}** isn't in frontend/src (missing: ${missing.map((f) => JSON.stringify(f)).join(", ")}) — renamed?`);
    }
    if (page.layer === "platform" && names.size) {
      // Check the platform SOURCE, not the merged page: overlays are allowed to name the customer.
      const src = fs.readFileSync(path.join(platformRoot, `${page.slug}.md`), "utf8");
      for (const n of names) {
        const re = new RegExp(`(^|[^\\p{L}\\p{N}])${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[^\\p{L}\\p{N}])`, "u");
        if (re.test(src)) errors.push(`${where}: names the customer ${JSON.stringify(n)} — platform pages are brand-neutral; put it in an overlay`);
      }
    }
    const reviewed = Date.parse(page.reviewed || "");
    if (Number.isNaN(reviewed)) warnings.push(`${where}: no 'reviewed: YYYY-MM-DD'`);
    else if ((today - reviewed) / 864e5 > STALE_DAYS) warnings.push(`${where}: last reviewed ${page.reviewed}, over 6 months ago`);
  }
  return { errors, warnings, pages: guide.manifest.pages.length };
}

function main() {
  const { errors, warnings, pages } = checkPages();
  warnings.forEach((w) => console.log(`WARN  ${w}`));
  errors.forEach((e) => console.log(`ERROR ${e}`));
  if (errors.length) {
    console.log(`user-guide check: ${errors.length} error(s)`);
    process.exit(1);
  }
  console.log(`user-guide check: ${pages} page(s) OK${warnings.length ? `, ${warnings.length} warning(s)` : ""}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
