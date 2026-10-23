// Screenshots for docs/user-guide/admin.md. File names match the page's image references.
// The viewer is a workspace (tenant) admin for yourcompany.com; all people and tags are invented.
import { json } from "./lib.mjs";

const DOMAIN = "yourcompany.com";
const TENANT = {
  domain: DOMAIN,
  display_name: "Your Company",
  documents_bucket: "yourcompany-documents",
  enabled_skills: ["demo-researcher", "phoenix-assistant", "contract-expert"],
  derived_group_tags: ["yourcompany-staff"],
  default_skill: "demo-researcher",
};
const USERS = {
  "sam.lee@yourcompany.com": { email: "sam.lee@yourcompany.com", uid: "u-sam", group_tags: ["finance-team"] },
};
const TAGS = [
  { id: "finance-team", label: "Finance team", description: "Finance staff: the budgeting skills.", grants: ["budget-assistant"] },
  { id: "yourcompany-staff", label: "Everyone at Your Company", description: "Granted to every user of the domain.", grants: [] },
  { id: "beta-tester", label: "Beta testers", description: "Early access to new skills.", grants: ["research-desk"] },
];
const PERMS = [
  { doc_id: "*", type: "wildcard", tools: ["*"], denied: [] },
  { doc_id: DOMAIN, type: "domain", tools: ["*"], denied: ["code_execution"] },
  { doc_id: "sam.lee@yourcompany.com", type: "user", tools: ["*"], denied: [] },
];
const ago = (m) => new Date(Date.now() - m * 60000).toISOString();
const AUDIT = {
  scope: "tenant",
  scanned: 3,
  entries: [
    {
      id: "a1",
      ts: ago(12),
      actorUid: "u-alex",
      actorEmail: "alex.admin@yourcompany.com",
      action: "user.group.grant",
      target: "sam.lee@yourcompany.com",
      before: { group_tags: [] },
      after: { group_tags: ["finance-team"] },
    },
    {
      id: "a2",
      ts: ago(95),
      actorUid: "u-alex",
      actorEmail: "alex.admin@yourcompany.com",
      action: "tenant.update",
      target: DOMAIN,
      before: { enabled_skills: ["demo-researcher"] },
      after: { enabled_skills: ["demo-researcher", "phoenix-assistant", "contract-expert"] },
    },
    {
      id: "a3",
      ts: ago(60 * 26),
      actorUid: "u-alex",
      actorEmail: "alex.admin@yourcompany.com",
      action: "tool_permissions.upsert",
      target: DOMAIN,
      before: null,
      after: { tools: ["*"], denied: ["code_execution"] },
    },
  ],
};

export function fixture() {
  return async function handle(route) {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace("/api/proxy", "");
    const m = req.method();
    let mm;
    if (p === "/api/admin/whoami") return json(route, 200, { scope: "tenant", domains: [DOMAIN] }), true;
    if (p === "/api/admin/clients" && m === "GET") return json(route, 200, [TENANT]), true;
    if ((mm = p.match(/^\/api\/admin\/clients\/([^/]+)$/)) && m === "PUT") {
      Object.assign(TENANT, req.postDataJSON() ?? {});
      return json(route, 200, TENANT), true;
    }
    if (/^\/api\/admin\/tenants\/[^/]+\/validate$/.test(p)) {
      return json(route, 200, {
        domain: DOMAIN,
        ok: true,
        checks: [
          { field: "documents_bucket", level: "ok", message: "The service account can read yourcompany-documents." },
          { field: "default_skill", level: "ok", message: "Demo Researcher exists and is enabled." },
          { field: "derived_group_tags", level: "warning", message: "yourcompany-staff grants no skills yet." },
        ],
      }), true;
    }
    if (p === "/api/admin/access/check" && m === "POST") {
      const { email } = req.postDataJSON() ?? {};
      const skills = [
        { skillId: "demo-researcher", slug: "demo-researcher", label: "Demo Researcher", visible: true, reason: "Shared with everyone at yourcompany.com, and enabled for the tenant.", accessAllowed: true, tenantAllowed: true, adminBypass: false },
        { skillId: "phoenix-assistant", slug: "phoenix-assistant", label: "Phoenix Assistant", visible: true, reason: "Shared with the finance-team group, which this user holds.", accessAllowed: true, tenantAllowed: true, adminBypass: false },
        { skillId: "research-desk", slug: "research-desk", label: "Research Desk", visible: false, reason: "Not in the tenant's enabled skills.", accessAllowed: true, tenantAllowed: false, adminBypass: true },
      ];
      return json(route, 200, {
        email,
        domain: DOMAIN,
        userFound: true,
        tags: [
          { tag: "finance-team", provenances: ["direct"] },
          { tag: "yourcompany-staff", provenances: ["domain"] },
        ],
        skillVisibility: {
          enabledSkills: TENANT.enabled_skills,
          visibleCount: 2,
          totalCount: 3,
          hiddenByTenantFilter: ["Research Desk"],
          skills,
        },
      }), true;
    }
    if ((mm = p.match(/^\/api\/admin\/users\/([^/]+)\/groups(?:\/([^/]+))?$/))) {
      const u = USERS[decodeURIComponent(mm[1])];
      if (m === "POST") u.group_tags = [...new Set([...u.group_tags, req.postDataJSON().tag])];
      if (m === "DELETE") u.group_tags = u.group_tags.filter((t) => t !== decodeURIComponent(mm[2]));
      return json(route, 200, u), true;
    }
    if ((mm = p.match(/^\/api\/admin\/users\/([^/]+)$/))) {
      const u = USERS[decodeURIComponent(mm[1])];
      return json(route, u ? 200 : 404, u ?? { detail: "not found" }), true;
    }
    if (p === "/api/admin/group-tags" && m === "GET") return json(route, 200, TAGS), true;
    if ((mm = p.match(/^\/api\/admin\/groups\/([^/]+)\/members$/))) {
      const tag = decodeURIComponent(mm[1]);
      const members = Object.values(USERS).filter((u) => u.group_tags.includes(tag)).map(({ email, uid }) => ({ email, uid }));
      return json(route, 200, { tag, members, scanned: 42, truncated: false, note: "Scanned 42 users in yourcompany.com." }), true;
    }
    if (p === "/api/admin/tool-permissions" && m === "GET") return json(route, 200, PERMS), true;
    if (p === "/api/admin/audit") return json(route, 200, AUDIT), true;
    return false;
  };
}

export default async function admin({ page, base, tidy, around }) {
  await page.setViewportSize({ width: 1280, height: 1100 });
  const main = () => page.locator("main").last();
  const crop = around(main);
  const go = async (path) => {
    await page.goto(`${base}${path}`, { waitUntil: "networkidle", timeout: 90000 });
    await tidy();
  };

  // Admin home
  await go("/admin");
  await page.getByRole("heading", { name: "Administration" }).waitFor({ timeout: 30000 });
  await crop("01-admin-home", page.getByRole("heading", { name: "Administration" }), page.locator("section").last());

  // Tenant config + edit + validate
  await go("/admin/tenants");
  await page.getByRole("heading", { name: "Tenant administration" }).waitFor({ timeout: 30000 });
  await page.getByText(/healthy|warning/).first().waitFor({ timeout: 15000 });
  await crop("02-tenants", page.getByRole("heading", { name: "Tenant administration" }), page.locator("table"));
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("button", { name: "Validate" }).click();
  await page.getByText("Validation passed (warnings are non-blocking).").waitFor();
  const editor = page.getByRole("heading", { name: `Edit ${DOMAIN}` }).locator("..");
  await crop("03-tenant-edit", page.getByRole("heading", { name: `Edit ${DOMAIN}` }), editor);
  await page.getByRole("button", { name: "Cancel" }).click();

  // What your users actually see
  await page.getByRole("button", { name: "What users see" }).click();
  await page.getByRole("textbox", { name: "User email to inspect" }).fill("sam.lee@yourcompany.com");
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await page.getByText(/2 of 3 skills visible/).waitFor();
  const panel = page.getByRole("heading", { name: "What your users actually see" }).locator("..");
  await crop("04-what-users-see", page.getByRole("heading", { name: DOMAIN, exact: true }), panel);

  // Users: look up, grant, effective access
  await go("/admin/users");
  await page.getByPlaceholder("user@example.com").fill("sam.lee@yourcompany.com");
  await page.getByRole("button", { name: "Look up" }).click();
  await page.getByText("u-sam").waitFor();
  await page.getByRole("button", { name: "Show effective access" }).click();
  await page.getByText(/Effective tags/).waitFor();
  // A tag being typed (the field's own placeholder names deployment-specific tags).
  await page.getByPlaceholder(/Add a tag/).fill("beta-tester");
  await crop("05-users", page.getByRole("heading", { name: "Users & Groups" }), page.locator("section").last());

  // Group tags (platform admins)
  await go("/admin/groups");
  await page.getByRole("heading", { name: "Group tags" }).waitFor({ timeout: 30000 });
  await page.getByRole("row", { name: /finance-team/ }).getByRole("button", { name: "Holders" }).click();
  await page.getByText(/Holders of/).waitFor();
  // Stops above "Add / update a tag", whose placeholders name deployment-specific tags.
  await crop("06-group-tags", page.getByRole("heading", { name: "Group tags" }), page.locator("section").filter({ hasText: "Holders of" }));

  // Tool permissions
  await go("/admin/tool-permissions");
  await page.getByRole("heading", { name: "Tool permissions" }).waitFor({ timeout: 30000 });
  await crop("07-tool-permissions", page.getByRole("heading", { name: "Tool permissions" }), page.locator("table"));

  // Audit trail
  await go("/admin/audit");
  await page.getByRole("heading", { name: "Audit trail" }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Diff" }).first().click();
  await crop("08-audit", page.getByRole("heading", { name: "Audit trail" }), page.locator("table"));
}
