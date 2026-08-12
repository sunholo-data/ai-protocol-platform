import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SkillSwitcher, isSystemSkill, isTestSkill } from "../SkillSwitcher";
import type { Skill } from "@/types/skill";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

function makeSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    name: "research",
    description: "",
    instructions: "",
    skillMetadata: {
      author: "test",
      version: "1.0",
      model: "gemini-2.5-flash",
      tools: [],
      toolConfigs: {},
      subSkills: [],
    },
    references: {},
    assets: {},
    skillId: "skill-1",
    slug: null,
    displayName: "Research",
    avatar: "",
    ownerEmail: "u@example.com",
    ownerId: "uid-1",
    accessControl: { type: "private" },
    protocols: {
      mcp: { enabled: false },
      a2a: { enabled: false },
      agui: { enabled: true },
      a2ui: { enabled: false },
      mcpApps: { enabled: false },
    },
    initialMessage: "",
    tags: [],
    featured: false,
    usageCount: 0,
    createdAt: 0,
    updatedAt: 0,
    ...overrides,
  };
}

beforeEach(() => {
  pushMock.mockClear();
});

describe("isTestSkill", () => {
  it("is false for a production skill (no admin/test tags)", () => {
    expect(isTestSkill(makeSkill({ tags: [] }))).toBe(false);
    expect(isTestSkill(makeSkill({ tags: ["research"] }))).toBe(false);
  });

  it("is true when tags intersect the admin/test tag set", () => {
    for (const t of ["experimental", "dev-tool", "a2ui-demo", "demo", "workshop", "admin"]) {
      expect(isTestSkill(makeSkill({ tags: [t] }))).toBe(true);
    }
  });
});

describe("isSystemSkill", () => {
  it("is true only for the `system` tag", () => {
    expect(isSystemSkill(makeSkill({ tags: ["system"] }))).toBe(true);
    expect(isSystemSkill(makeSkill({ tags: ["studio", "admin", "system"] }))).toBe(true);
    expect(isSystemSkill(makeSkill({ tags: ["admin"] }))).toBe(false);
    expect(isSystemSkill(makeSkill({ tags: [] }))).toBe(false);
  });
});

describe("SkillSwitcher", () => {
  it("renders the active skill name in the trigger", () => {
    const skills = [
      makeSkill({ skillId: "s1", displayName: "Research" }),
      makeSkill({ skillId: "s2", displayName: "Writer" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="s2" />);
    expect(screen.getByLabelText("Switch skill")).toHaveTextContent("Writer");
  });

  it("shows 'Select a skill' when there is no active skill", () => {
    const skills = [makeSkill({ skillId: "s1", displayName: "Research" })];
    render(<SkillSwitcher skills={skills} activeSkillId="none" />);
    expect(screen.getByLabelText("Switch skill")).toHaveTextContent("Select a skill");
  });

  it("opens the dropdown on click and lists skills", async () => {
    const skills = [
      makeSkill({ skillId: "s1", displayName: "Research" }),
      makeSkill({ skillId: "s2", displayName: "Writer" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="s1" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    expect(screen.getByLabelText("Search skills")).toBeInTheDocument();
    // Both skills present in the list (Research also in trigger, so use getAllByText).
    expect(screen.getAllByText("Research").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Writer")).toBeInTheDocument();
  });

  it("filters the list as the user types in search", async () => {
    const skills = [
      makeSkill({ skillId: "s1", displayName: "Research" }),
      makeSkill({ skillId: "s2", displayName: "Writer" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="s1" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    await userEvent.type(screen.getByLabelText("Search skills"), "writ");
    expect(screen.getByText("Writer")).toBeInTheDocument();
    // "Research" no longer in the filtered list (only remaining ref would be trigger).
    const trigger = screen.getByLabelText("Switch skill");
    expect(within(trigger).getByText("Research")).toBeInTheDocument();
    // No list row for Research: outside the trigger there should be none.
    const researchMatches = screen.getAllByText("Research");
    expect(researchMatches).toHaveLength(1);
  });

  it("shows no group headers when all skills are production (clean picker)", async () => {
    const skills = [
      makeSkill({ skillId: "active", displayName: "ActiveSkill", tags: [] }),
      makeSkill({ skillId: "prod", displayName: "ProdSkill", tags: [] }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="active" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    // No internal jargon — a normal user just sees their skills.
    expect(screen.queryByText("Production")).toBeNull();
    expect(screen.queryByText("Admin & Test")).toBeNull();
    expect(screen.queryByText("In development")).toBeNull();
  });

  it("never lists system-tagged skills — not even via search", async () => {
    const skills = [
      makeSkill({ skillId: "prod", displayName: "ProdSkill", tags: [] }),
      // Real-world shape: the Skill Studio copilot carries admin AND system
      // tags — system must win over the "In development" grouping.
      makeSkill({
        skillId: "copilot",
        displayName: "Skill Studio Copilot",
        tags: ["studio", "admin", "system"],
      }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="prod" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    expect(screen.queryByText("Skill Studio Copilot")).toBeNull();
    expect(screen.queryByText("In development")).toBeNull();

    // Searching for it by name reveals nothing.
    await userEvent.type(screen.getByLabelText("Search skills"), "copilot");
    expect(screen.queryByText("Skill Studio Copilot")).toBeNull();
    expect(screen.getByText("No skills found")).toBeInTheDocument();
  });

  it("puts test-tagged skills under a subtle 'In development' divider", async () => {
    const skills = [
      makeSkill({ skillId: "active", displayName: "ActiveSkill", tags: [] }),
      makeSkill({ skillId: "prod", displayName: "ProdSkill", tags: [] }),
      makeSkill({ skillId: "test", displayName: "TestSkill", tags: ["experimental"] }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="active" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    // Only the admin-visible section is labelled — and with a neutral phrase.
    expect(screen.queryByText("Production")).toBeNull();
    expect(screen.queryByText("Admin & Test")).toBeNull();
    const devHeader = screen.getByText("In development");

    const prodRow = screen.getByText("ProdSkill").closest("button")!;
    const testRow = screen.getByText("TestSkill").closest("button")!;
    expect(prodRow).toBeInTheDocument();
    expect(testRow).toBeInTheDocument();
    // The production row precedes the "In development" divider, which precedes the test row.
    expect(prodRow.compareDocumentPosition(devHeader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(devHeader.compareDocumentPosition(testRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  function withCategory(category: string, overrides: Partial<Skill> = {}): Skill {
    const base = makeSkill(overrides);
    return { ...base, skillMetadata: { ...base.skillMetadata, category } };
  }

  it("pins + highlights the tenant default skill at the top under a 'Default' header", async () => {
    const skills = [
      withCategory("specialist", { skillId: "ppa", displayName: "PPA Expert" }),
      makeSkill({ skillId: "one", displayName: "ONE Assistant" }),
    ];
    // Active skill differs from the default so "ONE Assistant" appears once (in
    // the default row, not the trigger).
    render(<SkillSwitcher skills={skills} activeSkillId="ppa" defaultSkillId="one" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    const defaultHeader = screen.getByText("Default");
    const oneRow = screen.getByText("ONE Assistant").closest("button")!;
    const ppaHeader = screen.getByText("Specialists");
    // Default header + its row precede the Specialists section.
    expect(defaultHeader.compareDocumentPosition(oneRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(oneRow.compareDocumentPosition(ppaHeader) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("groups skills by category (Specialists / Assistants / Tools)", async () => {
    const skills = [
      withCategory("specialist", { skillId: "ppa", displayName: "PPA Expert" }),
      withCategory("assistant", { skillId: "asst", displayName: "General Helper" }),
      withCategory("tool", { skillId: "web", displayName: "Web Researcher" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="ppa" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    expect(screen.getByText("Specialists")).toBeInTheDocument();
    expect(screen.getByText("Assistants")).toBeInTheDocument();
    expect(screen.getByText("Tools")).toBeInTheDocument();
  });

  it("groups the signed-in user's own skills under 'Your skills'", async () => {
    const skills = [
      withCategory("specialist", { skillId: "ppa", displayName: "PPA Expert", ownerId: "aitana-platform" }),
      makeSkill({ skillId: "mine", displayName: "My Tool", ownerId: "uid-me" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="ppa" currentUserId="uid-me" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));

    const yoursHeader = screen.getByText("Your skills");
    const mineRow = screen.getByText("My Tool").closest("button")!;
    expect(yoursHeader.compareDocumentPosition(mineRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("labels the leftover 'Other skills' bucket only when a real category section exists", async () => {
    // No categories anywhere → the plain list has NO 'Other skills' header.
    const uncategorised = [
      makeSkill({ skillId: "a", displayName: "Alpha" }),
      makeSkill({ skillId: "b", displayName: "Beta" }),
    ];
    const { unmount } = render(<SkillSwitcher skills={uncategorised} activeSkillId="a" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));
    expect(screen.queryByText("Other skills")).toBeNull();
    unmount();

    // A category section present → the uncategorised leftover IS labelled.
    const mixed = [
      withCategory("specialist", { skillId: "ppa", displayName: "PPA Expert" }),
      makeSkill({ skillId: "misc", displayName: "Misc Skill" }),
    ];
    render(<SkillSwitcher skills={mixed} activeSkillId="ppa" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));
    expect(screen.getByText("Other skills")).toBeInTheDocument();
  });

  it("navigates via skillHref when a skill is selected", async () => {
    const skills = [
      makeSkill({ skillId: "s1", displayName: "Research", ownerId: "mark", slug: "research" }),
      makeSkill({ skillId: "s2", displayName: "Writer" }),
    ];
    render(<SkillSwitcher skills={skills} activeSkillId="s2" />);
    await userEvent.click(screen.getByLabelText("Switch skill"));
    await userEvent.click(screen.getByText("Research"));

    expect(pushMock).toHaveBeenCalledWith("/chat/@mark/research");
  });
});
