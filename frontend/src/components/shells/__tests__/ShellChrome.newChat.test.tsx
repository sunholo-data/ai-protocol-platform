// The header's "New chat" (ShellChrome → SkillsBar) keeps a new chat inside the
// project the open chat is in; leaving a project is the banner's ✕, never a
// side effect of starting a new chat (projects design, UI rework 2026-10-20).

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush, replace: vi.fn() }) }));
const skills = [{ skillId: "s1", slug: "assistant", ownerId: "platform", displayName: "Assistant", name: "assistant" }];
vi.mock("@/hooks/useUserSkills", () => ({ useUserSkills: () => ({ skills, isLoading: false, error: null }) }));
vi.mock("@/hooks/useTenantDefaultSkill", () => ({ useTenantDefaultSkill: () => "s1" }));
vi.mock("@/components/navigation/SkillsBar", () => ({
  SkillsBar: ({ onNewConversation }: { onNewConversation: () => void }) => (
    <button type="button" onClick={onNewConversation}>
      New chat
    </button>
  ),
}));

import { setActiveProject } from "@/lib/activeProject";
import { skillHref } from "@/components/navigation/skillHref";
import { ShellChrome } from "../ShellChrome";

const user = { uid: "u1" } as unknown as Parameters<typeof ShellChrome>[0]["user"];

afterEach(() => {
  setActiveProject(null);
  mockPush.mockReset();
});

describe("ShellChrome — New chat", () => {
  it("stays in the open chat's project", () => {
    setActiveProject("p1");
    render(
      <ShellChrome skillId="s1" user={user}>
        <div />
      </ShellChrome>,
    );
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(mockPush).toHaveBeenCalledWith(`${skillHref(skills[0] as never)}?project=p1`);
  });

  it("is a plain new chat outside a project", () => {
    render(
      <ShellChrome skillId="s1" user={user}>
        <div />
      </ShellChrome>,
    );
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(mockPush).toHaveBeenCalledWith(skillHref(skills[0] as never));
  });
});
