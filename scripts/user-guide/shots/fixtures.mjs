/**
 * The synthetic library every user-guide screenshot is taken against.
 * Invented people, folders, documents and skills only — see lib.mjs.
 *
 * `library()` returns a fresh, stateful fake of the folder / document /
 * knowledge-base / skill API, so a shot script can click through a flow
 * (rename, move, make searchable…) and the UI sees consistent results.
 */
import { json } from "./lib.mjs";

export const UID = "guide-user";
export const PLATFORM = "aitana-platform";

export function library() {
  const now = Date.now();
  const iso = (minsAgo) => new Date(now - minsAgo * 60000).toISOString();
  const folders = [
    { id: "f-phoenix", name: "Project Phoenix", createdAt: iso(10) },
    { id: "f-market", name: "Market research", createdAt: iso(30), knowledgeBaseId: "kb-market" },
    { id: "f-drafts", name: "Old drafts", createdAt: iso(60) },
    { id: "f-created", name: "Created", createdAt: iso(90), system: true },
  ];
  let docs = [
    ["d1", "f-phoenix", "Phoenix project brief.pdf", "pdf"],
    ["d2", "f-phoenix", "Phoenix budget 2026.xlsx", "xlsx"],
    ["d3", "f-phoenix", "Kick-off meeting notes.docx", "docx"],
    ["d4", "f-phoenix", "Risk register.csv", "csv"],
    ["d5", "f-market", "Competitor overview.pptx", "pptx"],
    ["d6", "f-market", "Customer survey results.xlsx", "xlsx"],
    ["d7", "f-market", "Industry trends 2026.pdf", "pdf"],
    ["d8", "f-created", "Kick-off summary.md", "md", "generated"],
    // Exists only to produce the "same name already there" conflict; hidden from listings.
    ["d9", "f-market", "Kick-off meeting notes.docx", "docx"],
  ].map(([id, folderId, originalFilename, sourceFormat, source], i) => ({
    id,
    folderId,
    originalFilename,
    sourceFormat,
    source: source ?? "uploaded",
    parseStatus: "parsed",
    parseError: null,
    userId: UID,
    blockCount: 20 + i,
    createdAt: iso(5 + i),
    updatedAt: iso(5 + i),
  }));
  const kbs = {
    "kb-market": {
      kbId: "kb-market",
      name: "Market research",
      documentCount: 3,
      audience: "only you",
      canManage: true,
      accessControl: { type: "private" },
      lastSyncSummary: { state: "done", total: 3, done: 3, failed: 0 },
    },
  };
  const skills = [
    {
      skillId: "demo-researcher",
      slug: "demo-researcher",
      name: "demo-researcher",
      displayName: "Demo Researcher",
      description: "Researches a topic and cites its sources.",
      ownerId: PLATFORM,
      accessControl: { type: "domain", domain: "yourcompany.com" },
      managedBy: null,
      canEdit: true,
      skillMetadata: { tools: [] },
    },
    {
      skillId: "phoenix-assistant",
      slug: "phoenix-assistant",
      name: "phoenix-assistant",
      displayName: "Phoenix Assistant",
      description: "Answers questions about Project Phoenix.",
      ownerId: UID,
      accessControl: { type: "private" },
      managedBy: null,
      canEdit: true,
      skillMetadata: { tools: [] },
    },
    {
      skillId: "contract-expert",
      slug: "contract-expert",
      name: "contract-expert",
      displayName: "Contract Expert",
      description: "Reviews contracts and explains their terms.",
      ownerId: "colleague",
      accessControl: { type: "domain", domain: "yourcompany.com" },
      managedBy: null,
      canEdit: false,
      skillMetadata: { tools: [] },
    },
    {
      skillId: "research-desk",
      slug: "research-desk",
      name: "research-desk",
      displayName: "Research Desk",
      description: "Searches your own searchable folders.",
      ownerId: "colleague",
      accessControl: { type: "domain", domain: "yourcompany.com" },
      managedBy: null,
      canEdit: false,
      skillMetadata: { tools: ["knowledge_search"], toolConfigs: { knowledge_search: { include_my_folders: true } } },
    },
  ];
  let kbCreatedAt = null;
  let kbTotal = 0;
  let kbIndexed = 0;
  let uploads = 0;

  const folderJson = (f) => {
    const ds = docs.filter((d) => d.folderId === f.id && d.id !== "d9");
    return { userId: UID, docCount: ds.length, parsedCount: ds.length, system: false, knowledgeBaseId: null, ...f };
  };

  /** Answer one /api/proxy call; return false when this fixture doesn't cover it. */
  async function handle(route) {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace("/api/proxy", "");
    const m = req.method();
    let mm;

    // App shell: a healthy backend, a signed-in member with no sessions yet.
    if (p === "/health") return json(route, 200, { status: "ok" }), true;
    if (p === "/api/local-mode-status") return json(route, 200, { local_mode: false }), true;
    if (p === "/api/auth/whoami") {
      return json(route, 200, { uid: UID, email: "you@yourcompany.com", domain: "yourcompany.com", groupTags: [] }), true;
    }
    if (p === "/api/push/config") return json(route, 200, { enabled: false }), true;
    if (p === "/api/admin/whoami") return json(route, 200, { scope: "none", domains: [] }), true;
    if (p === "/api/clients/me") return json(route, 200, { domain: "yourcompany.com" }), true;
    if (p === "/api/sessions" && m === "GET") return json(route, 200, { sessions: [] }), true;
    if (/^\/api\/sessions\/[^/]+\/(bootstrap|activity)$/.test(p)) return json(route, 200, { events: [] }), true;
    // Reading back the current session: an empty one (a 404 makes the app start a
    // new session on every check). Pages with a real conversation answer first.
    if ((mm = p.match(/^\/api\/sessions\/([^/]+)(\/messages)?$/)) && m === "GET") {
      return json(route, 200, mm[2] ? { messages: [] } : { sessionId: mm[1], skillId: "demo-researcher", title: "", messages: [] }), true;
    }
    if (p === "/api/voice/config") return json(route, 200, { enabled: false }), true;
    // No original file bytes in the fixture: the app falls back to the Text view.
    if (/^\/api\/documents\/[^/]+\/(preview|thumbnail)$/.test(p)) return json(route, 404, { detail: "no file in the fixture" }), true;
    if (p === "/api/folders" && m === "GET") return json(route, 200, { folders: folders.map(folderJson) }), true;
    if ((mm = p.match(/^\/api\/folders\/([^/]+)\/documents$/))) {
      return json(route, 200, { documents: docs.filter((d) => d.folderId === mm[1] && d.id !== "d9") }), true;
    }
    if ((mm = p.match(/^\/api\/folders\/([^/]+)$/))) {
      const f = folders.find((x) => x.id === mm[1]);
      if (m === "PATCH") {
        const { name } = req.postDataJSON();
        if (folders.some((x) => x.name === name && x.id !== f.id)) {
          return json(route, 409, { detail: { code: "FOLDER_EXISTS", message: `You already have a folder called '${name}'.` } }), true;
        }
        f.name = name;
        return json(route, 200, folderJson(f)), true;
      }
      if (m === "DELETE") {
        const n = folderJson(f).docCount;
        if (n) {
          const message = `'${f.name}' still has ${n} document${n === 1 ? "" : "s"}. Move or delete them first.`;
          return json(route, 409, { detail: { code: "FOLDER_NOT_EMPTY", message, count: n } }), true;
        }
        folders.splice(folders.indexOf(f), 1);
        return route.fulfill({ status: 204 }), true;
      }
    }
    if (p === "/api/documents/upload" && m === "POST") {
      uploads += 1;
      await new Promise((r) => setTimeout(r, 900));
      const body = req.postDataBuffer()?.toString("latin1") ?? "";
      const base = ((body.match(/filename="([^"]+)"/) || [])[1] || "file").split("/").pop();
      const folderPath = (body.match(/name="folder_path"\r\n\r\n([^\r]*)/) || [])[1] || "Uploads";
      if (base.endsWith(".exe")) return json(route, 400, { detail: `Unsupported file type: ${base}` }), true;
      let f = folders.find((x) => x.name === folderPath);
      if (!f) {
        f = { id: `f-${folderPath.replace(/\W+/g, "-").toLowerCase()}`, name: folderPath, createdAt: iso(0) };
        folders.unshift(f);
      }
      const unchanged = base === "Board pack Q1.pdf";
      if (!docs.some((d) => d.folderId === f.id && d.originalFilename === base)) {
        docs.push({
          id: `u${uploads}`,
          folderId: f.id,
          originalFilename: base,
          sourceFormat: base.split(".").pop(),
          source: "uploaded",
          parseStatus: "parsed",
          parseError: null,
          userId: UID,
          blockCount: 12,
          createdAt: iso(0),
          updatedAt: iso(0),
        });
      }
      return json(route, 200, { id: `u${uploads}`, folderId: f.id, originalFilename: base, parseStatus: "parsed", unchanged }), true;
    }
    if ((mm = p.match(/^\/api\/documents\/([^/]+)$/)) && m === "GET") {
      const d = docs.find((x) => x.id === mm[1]);
      return json(route, d ? 200 : 404, d ?? { detail: "not found" }), true;
    }
    if ((mm = p.match(/^\/api\/documents\/([^/]+)$/)) && m === "PATCH") {
      const { folder_id: folderId, replace, filename } = req.postDataJSON();
      const d = docs.find((x) => x.id === mm[1]);
      if (typeof filename === "string") {
        // Rename: like the backend, keep the extension when it's left off.
        const ext = (d.originalFilename.match(/\.[^.]+$/) || [""])[0];
        let name = filename.trim();
        if (ext && !name.toLowerCase().endsWith(ext.toLowerCase())) name += ext;
        d.originalFilename = name;
        return json(route, 200, d), true;
      }
      const target = folders.find((x) => x.id === folderId);
      const clash = docs.find((x) => x.folderId === folderId && x.originalFilename === d.originalFilename && x.id !== d.id);
      if (clash && !replace) {
        const message = `'${target.name}' already has a document called '${d.originalFilename}'.`;
        return json(route, 409, { detail: { code: "FILENAME_EXISTS", message } }), true;
      }
      if (clash) docs = docs.filter((x) => x !== clash);
      d.folderId = folderId;
      return json(route, 200, d), true;
    }
    if (p === "/api/knowledge-bases" && m === "GET") {
      if (kbCreatedAt) {
        // The shot script advances indexing itself (lib.indexed(n)), so each
        // badge state is on screen exactly when it is photographed.
        const total = kbTotal;
        const done = Math.min(total, kbIndexed);
        Object.assign(kbs["kb-phoenix"], {
          lastSyncSummary: { state: done >= total ? "done" : "indexing", total, done, failed: 0 },
          documentCount: done,
        });
      }
      return json(route, 200, Object.values(kbs)), true;
    }
    if (p === "/api/knowledge-bases" && m === "POST") {
      const { folderId } = req.postDataJSON();
      const f = folders.find((x) => x.id === folderId);
      f.knowledgeBaseId = "kb-phoenix";
      kbs["kb-phoenix"] = {
        kbId: "kb-phoenix",
        name: f.name,
        documentCount: 0,
        audience: "only you",
        canManage: true,
        accessControl: { type: "private" },
        lastSyncSummary: { state: "queued" },
      };
      kbCreatedAt = Date.now() + 1500;
      kbTotal = folderJson(f).docCount;
      return json(route, 201, kbs["kb-phoenix"]), true;
    }
    if (p === "/api/knowledge-bases/_check" && m === "POST") {
      const { accessControl } = req.postDataJSON();
      if (accessControl?.type === "private") {
        return json(route, 200, { ok: true, results: [{ ref: "kb-phoenix", ok: true, reason: "" }] }), true;
      }
      const results = [{ ref: "kb-phoenix", ok: false, reason: "The folder is narrower than the skill.", widenTo: accessControl, canWiden: true }];
      return json(route, 200, { ok: false, results }), true;
    }
    if ((mm = p.match(/^\/api\/knowledge-bases\/([^/]+)$/)) && m === "DELETE") {
      delete kbs[mm[1]];
      folders.forEach((f) => {
        if (f.knowledgeBaseId === mm[1]) f.knowledgeBaseId = null;
      });
      return route.fulfill({ status: 204 }), true;
    }
    if ((mm = p.match(/^\/api\/knowledge-bases\/([^/]+)$/)) && m === "PATCH") {
      return json(route, 200, kbs[mm[1]] ?? {}), true;
    }
    if (p === "/api/skills" && m === "GET") return json(route, 200, skills), true;
    if ((mm = p.match(/^\/api\/skills\/by-slug\/@?([^/]+)\/([^/]+)$/))) {
      const s = skills.find((x) => x.slug === mm[2]);
      return json(route, s ? 200 : 404, s ?? { detail: "not found" }), true;
    }
    if ((mm = p.match(/^\/api\/skills\/([^/]+)\/fork$/)) && m === "POST") {
      const src = skills.find((x) => x.skillId === mm[1]);
      const copy = { ...src, skillId: `${src.skillId}-fork`, slug: `${src.slug}-fork`, displayName: `${src.displayName} (Fork)`, ownerId: UID, canEdit: true };
      skills.push(copy);
      return json(route, 201, copy), true;
    }
    if ((mm = p.match(/^\/api\/skills\/([^/]+)$/))) {
      const s = skills.find((x) => x.skillId === mm[1]);
      if (m === "PUT" || m === "PATCH") Object.assign(s, req.postDataJSON() ?? {});
      return json(route, s ? 200 : 404, s ?? { detail: "not found" }), true;
    }
    return false;
  }

  return {
    handle,
    folders,
    skills,
    kbs,
    /** Set how many documents of the folder being made searchable are indexed. */
    indexed: (n) => {
      kbIndexed = n;
    },
  };
}
