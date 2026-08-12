import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SkillsBar } from "../SkillsBar";
import type { Skill } from "@/types/skill";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// SkillsBar now renders <UserMenu>, which calls useAuth — stub it so the bar
// renders without a real AuthProvider.
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    signIn: vi.fn(),
    signOut: vi.fn(),
    getIdToken: vi.fn(),
    signInWithRedirect: vi.fn(),
  }),
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

describe("SkillsBar", () => {
  it("renders the skill switcher with the active skill name", () => {
    const skills = [
      makeSkill({ skillId: "s1", displayName: "Research" }),
      makeSkill({ skillId: "s2", displayName: "Writer" }),
    ];
    render(
      <SkillsBar
        skills={skills}
        activeSkillId="s2"
        isLoading={false}
        onCreateClick={() => {}}
        onConfigureClick={() => {}}
        onNewConversation={() => {}}
      />,
    );

    // The switcher trigger shows the active skill.
    const trigger = screen.getByLabelText("Switch skill");
    expect(trigger).toHaveTextContent("Writer");
  });

  it("renders skeleton when loading", () => {
    render(
      <SkillsBar
        skills={[]}
        activeSkillId=""
        isLoading={true}
        onCreateClick={() => {}}
        onConfigureClick={() => {}}
        onNewConversation={() => {}}
      />,
    );
    expect(screen.getByTestId("skill-tabs-skeleton")).toBeInTheDocument();
  });

  it("shows empty state when user has no skills", () => {
    render(
      <SkillsBar
        skills={[]}
        activeSkillId=""
        isLoading={false}
        onCreateClick={() => {}}
        onConfigureClick={() => {}}
        onNewConversation={() => {}}
      />,
    );
    expect(screen.getByText(/no skills yet/i)).toBeInTheDocument();
  });

  it("calls onCreateClick when the create button is pressed", async () => {
    const handleCreate = vi.fn();
    render(
      <SkillsBar
        skills={[]}
        activeSkillId=""
        isLoading={false}
        onCreateClick={handleCreate}
        onConfigureClick={() => {}}
        onNewConversation={() => {}}
      />,
    );
    await userEvent.click(screen.getByLabelText(/create a new skill/i));
    expect(handleCreate).toHaveBeenCalledTimes(1);
  });

  it("calls onConfigureClick when the config gear is pressed (active skill present)", async () => {
    const handleConfigure = vi.fn();
    const skills = [makeSkill({ skillId: "s1", displayName: "Research" })];
    render(
      <SkillsBar
        skills={skills}
        activeSkillId="s1"
        isLoading={false}
        onCreateClick={() => {}}
        onConfigureClick={handleConfigure}
        onNewConversation={() => {}}
      />,
    );
    await userEvent.click(screen.getByLabelText(/configure this skill/i));
    expect(handleConfigure).toHaveBeenCalledTimes(1);
  });

  it("calls onNewConversation when the New chat button is pressed", async () => {
    const handleNew = vi.fn();
    const skills = [makeSkill({ skillId: "s1", displayName: "Research" })];
    render(
      <SkillsBar
        skills={skills}
        activeSkillId="s1"
        isLoading={false}
        onCreateClick={() => {}}
        onConfigureClick={() => {}}
        onNewConversation={handleNew}
      />,
    );
    await userEvent.click(screen.getByLabelText(/start a new conversation/i));
    expect(handleNew).toHaveBeenCalledTimes(1);
  });

  it("hides the config gear when there is no active skill", () => {
    const skills = [makeSkill({ skillId: "s1", displayName: "Research" })];
    render(
      <SkillsBar
        skills={skills}
        activeSkillId="not-a-skill"
        isLoading={false}
        onCreateClick={() => {}}
        onConfigureClick={() => {}}
        onNewConversation={() => {}}
      />,
    );
    expect(screen.queryByLabelText(/configure this skill/i)).toBeNull();
  });
});
