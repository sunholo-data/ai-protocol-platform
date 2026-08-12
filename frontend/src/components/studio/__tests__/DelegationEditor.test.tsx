import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { DelegationEditor } from "../DelegationEditor";
import type { DelegationConfig } from "@/types/skill";

const fetchWithAuth = vi.fn();
vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: (...args: unknown[]) => fetchWithAuth(...args) }));

function mockSkills(list: Array<{ skillId: string; displayName: string }>) {
  fetchWithAuth.mockResolvedValue({ json: async () => list });
}

beforeEach(() => {
  fetchWithAuth.mockReset();
  mockSkills([
    { skillId: "self", displayName: "This Skill" },
    { skillId: "ppa", displayName: "PPA Specialist" },
    { skillId: "web", displayName: "Web Researcher" },
  ]);
});

describe("DelegationEditor", () => {
  it("hides mode + picker until enabled", () => {
    render(<DelegationEditor value={{ enabled: false, mode: "auto", allow: [] }} onChange={vi.fn()} />);
    expect(screen.queryByText(/Automatic/i)).toBeNull();
  });

  it("lists access-scoped skills and excludes the current skill", async () => {
    render(
      <DelegationEditor
        value={{ enabled: true, mode: "auto", allow: [] }}
        currentSkillId="self"
        onChange={vi.fn()}
      />,
    );
    await waitFor(() => expect(screen.getByText("PPA Specialist")).toBeInTheDocument());
    expect(screen.getByText("Web Researcher")).toBeInTheDocument();
    expect(screen.queryByText("This Skill")).toBeNull(); // current skill excluded
  });

  it("toggles a delegate into allow via onChange", async () => {
    const onChange = vi.fn();
    render(
      <DelegationEditor value={{ enabled: true, mode: "auto", allow: [] }} currentSkillId="self" onChange={onChange} />,
    );
    await waitFor(() => screen.getByText("PPA Specialist"));
    fireEvent.click(screen.getByLabelText(/PPA Specialist/i));
    const next = onChange.mock.calls.at(-1)?.[0] as DelegationConfig;
    expect(next.allow).toContain("ppa");
  });

  it("switches mode to suggest", async () => {
    const onChange = vi.fn();
    render(
      <DelegationEditor value={{ enabled: true, mode: "auto", allow: [] }} currentSkillId="self" onChange={onChange} />,
    );
    await waitFor(() => screen.getByText(/Suggest/i));
    fireEvent.click(screen.getByRole("radio", { name: /Suggest/i }));
    const next = onChange.mock.calls.at(-1)?.[0] as DelegationConfig;
    expect(next.mode).toBe("suggest");
  });

  // Regression: the ONE front door serializes `allow` as {skill, floor} rules,
  // not bare strings. Rendering the raw object as a React child crashed Studio
  // ("Objects are not valid as a React child").
  describe("object-form allow ({skill, floor})", () => {
    it("renders (checked) without crashing", async () => {
      render(
        <DelegationEditor
          value={{ enabled: true, mode: "auto", allow: [{ skill: "ppa", floor: "auto" }] }}
          currentSkillId="self"
          onChange={vi.fn()}
        />,
      );
      await waitFor(() => screen.getByText("PPA Specialist"));
      expect((screen.getByLabelText(/PPA Specialist/i) as HTMLInputElement).checked).toBe(true);
    });

    it("renders an orphan object entry as its id, not '[object Object]'", async () => {
      render(
        <DelegationEditor
          value={{ enabled: true, mode: "auto", allow: [{ skill: "retired-skill", floor: "confirm" }] }}
          currentSkillId="self"
          onChange={vi.fn()}
        />,
      );
      await waitFor(() => screen.getByText("retired-skill"));
      expect(screen.queryByText(/\[object Object\]/)).toBeNull();
    });

    it("preserves other entries' {skill, floor} shape when toggling one off", async () => {
      const onChange = vi.fn();
      render(
        <DelegationEditor
          value={{
            enabled: true,
            mode: "auto",
            allow: [
              { skill: "ppa", floor: "confirm" },
              { skill: "web", floor: "auto" },
            ],
          }}
          currentSkillId="self"
          onChange={onChange}
        />,
      );
      await waitFor(() => screen.getByText("Web Researcher"));
      fireEvent.click(screen.getByLabelText(/Web Researcher/i)); // uncheck web
      const next = onChange.mock.calls.at(-1)?.[0] as DelegationConfig;
      expect(next.allow).toEqual([{ skill: "ppa", floor: "confirm" }]); // ppa's floor kept
    });
  });
});
