import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

// Mock the three shells to lightweight markers so the dispatch is tested in
// isolation (ChatShell pulls the full chat dependency graph otherwise).
vi.mock("@/components/chat/ChatShell", () => ({
  ChatShell: () => <div data-testid="chat-shell" />,
}));
vi.mock("@/components/shells/DocCompareShell", () => ({
  DocCompareShell: () => <div data-testid="doc-compare-shell" />,
}));
vi.mock("@/components/shells/WorkbenchShell", () => ({
  WorkbenchShell: () => <div data-testid="workbench-shell" />,
}));

// ShellChrome is NOT mocked — we want to prove the shared top nav (Home link)
// wraps every mode. It uses useRouter; user.uid is undefined so useUserSkills
// no-ops (no fetch).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// ShellChrome → SkillsBar → UserMenu calls useAuth; stub it (no AuthProvider
// in this test tree).
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

import { ShellRouter } from "@/components/shells/ShellRouter";
import type { SkillShell } from "@/types/skill";

const baseProps = {
  skillId: "skill-1",
  pathPrefix: "/chat/@owner/slug",
  user: {} as never,
};

function renderWith(shell: SkillShell | null) {
  return render(<ShellRouter {...baseProps} shell={shell} />);
}

describe("ShellRouter dispatch", () => {
  it("renders ChatShell for chat-primary", () => {
    renderWith({ mode: "chat-primary" });
    expect(screen.queryByTestId("chat-shell")).not.toBeNull();
    expect(screen.queryByTestId("doc-compare-shell")).toBeNull();
  });

  it("renders DocCompareShell for doc-compare", () => {
    renderWith({ mode: "doc-compare" });
    expect(screen.queryByTestId("doc-compare-shell")).not.toBeNull();
    expect(screen.queryByTestId("chat-shell")).toBeNull();
  });

  it("renders WorkbenchShell for workbench-primary", () => {
    renderWith({ mode: "workbench-primary" });
    expect(screen.queryByTestId("workbench-shell")).not.toBeNull();
    expect(screen.queryByTestId("chat-shell")).toBeNull();
  });

  it("falls back to ChatShell for a null shell (legacy skill)", () => {
    renderWith(null);
    expect(screen.queryByTestId("chat-shell")).not.toBeNull();
  });

  it("falls back to ChatShell for custom mode (v1 resolves custom to ChatShell)", () => {
    renderWith({ mode: "custom" });
    expect(screen.queryByTestId("chat-shell")).not.toBeNull();
  });

  it("falls back to ChatShell for an unknown/forward-compat mode", () => {
    renderWith({ mode: "holographic" as unknown as SkillShell["mode"] });
    expect(screen.queryByTestId("chat-shell")).not.toBeNull();
  });

  // v6.6.0: the shared top nav (Home + skill switcher) wraps EVERY shell mode
  // so a user routed into a specialised shell is never stranded without a way
  // to navigate home or switch skills.
  it.each([
    ["chat-primary", "chat-shell"],
    ["doc-compare", "doc-compare-shell"],
    ["workbench-primary", "workbench-shell"],
  ] as const)("renders ShellChrome (Home nav) around %s", (mode, testId) => {
    renderWith({ mode });
    expect(screen.queryByTestId(testId)).not.toBeNull();
    expect(screen.queryByLabelText("Home")).not.toBeNull();
  });
});
