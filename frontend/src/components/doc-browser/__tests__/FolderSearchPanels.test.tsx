import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const checkBinding = vi.fn();
const addKnowledgeBaseToSkill = vi.fn();
const setKnowledgeBaseAccess = vi.fn();
const forkSkill = vi.fn();
vi.mock("@/lib/knowledgeBases", async (importOriginal) => ({
  KNOWLEDGE_TOOL: "knowledge_search",
  searchesMyFolders: (await importOriginal<typeof import("@/lib/knowledgeBases")>()).searchesMyFolders,
  forkSkill: (...a: unknown[]) => forkSkill(...a),
  checkBinding: (...a: unknown[]) => checkBinding(...a),
  addKnowledgeBaseToSkill: (...a: unknown[]) => addKnowledgeBaseToSkill(...a),
  setKnowledgeBaseAccess: (...a: unknown[]) => setKnowledgeBaseAccess(...a),
}));

import { fetchWithAuth } from "@/lib/apiClient";
import { AddToSkillPanel, SearchAccessPanel } from "../FolderSearchPanels";

const kb = { kbId: "llm-1", name: "llm", documentCount: 11, audience: "only its owner", canManage: true, accessControl: { type: "private" } };
const skills = [
  { skillId: "mine", displayName: "My research", ownerId: "mark", accessControl: { type: "private" }, managedBy: "firestore" },
  { skillId: "default", displayName: "Sunholo", ownerId: "platform", accessControl: { type: "tagged", tags: ["sunholo-staff"] }, managedBy: "template" },
  { skillId: "team", displayName: "Team helper", ownerId: "mark", accessControl: { type: "tagged", tags: ["sunholo-staff"] }, managedBy: "firestore" },
  { skillId: "theirs", displayName: "Someone else's", ownerId: "eve", accessControl: { type: "private" } },
  { skillId: "shared", displayName: "Contract Expert", ownerId: "eve", accessControl: { type: "domain", domain: "sunholo.com" }, canEdit: false },
  {
    skillId: "roomy",
    displayName: "Research desk",
    ownerId: "eve",
    accessControl: { type: "domain", domain: "sunholo.com" },
    canEdit: false,
    skillMetadata: { tools: ["knowledge_search"], toolConfigs: { knowledge_search: { include_my_folders: true } } },
  },
];

function res(body: unknown, status = 200): Response {
  return { ok: status < 300, status, json: async () => body } as unknown as Response;
}

beforeEach(() => {
  vi.mocked(fetchWithAuth).mockReset().mockResolvedValue(res(skills));
  checkBinding.mockReset();
  addKnowledgeBaseToSkill.mockReset().mockResolvedValue({ ok: true, value: {} });
  forkSkill.mockReset().mockResolvedValue({
    ok: true,
    value: { skillId: "fork-1", slug: "contract-expert-fork", ownerId: "mark", displayName: "Contract Expert (Fork)" },
  });
  setKnowledgeBaseAccess.mockReset().mockResolvedValue({ ok: true, value: {} });
});

function renderAdd(currentSkillId = "mine") {
  return render(<AddToSkillPanel kb={kb} folderName="llm" uid="mark" currentSkillId={currentSkillId} onClose={vi.fn()} />);
}

describe("AddToSkillPanel", () => {
  it("lists the current skill first plus your own, and adds when allowed", async () => {
    checkBinding.mockResolvedValue({ ok: true, value: { ok: true, results: [{ ref: "llm-1", kbId: "llm-1", ok: true, reason: "" }] } });
    renderAdd();
    const select = await screen.findByRole("combobox", { name: "Skill" });
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["My research (this chat)", "Team helper"]);
    await userEvent.click(await screen.findByRole("button", { name: "Add to My research" }));
    expect(addKnowledgeBaseToSkill).toHaveBeenCalledWith("mine", "llm-1");
    expect(await screen.findByRole("status")).toHaveTextContent("My research can now search ‘llm’");
    // The confirmation stays — it is not wiped when the skill's local copy updates.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByRole("status")).toHaveTextContent("My research can now search ‘llm’");
  });

  it("explains a wider skill and offers to share the folder with exactly its audience", async () => {
    checkBinding.mockResolvedValue({
      ok: true,
      value: { ok: false, results: [{ ref: "llm-1", kbId: "llm-1", ok: false, reason: "r", widenTo: { type: "tagged", tags: ["sunholo-staff"] }, canWiden: true }] },
    });
    renderAdd("team");
    expect(await screen.findByText(/Adding it would let them search these documents/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Share ‘llm’ with group sunholo-staff and add" }));
    expect(setKnowledgeBaseAccess).toHaveBeenCalledWith("llm-1", { type: "tagged", tags: ["sunholo-staff"] });
    expect(addKnowledgeBaseToSkill).toHaveBeenCalledWith("team", "llm-1");
  });

  it("says up front that a built-in skill can't keep the folder, and offers your own copy", async () => {
    renderAdd("default");
    expect(await screen.findByText(/You can’t change Sunholo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make my copy and add ‘llm’" })).toBeInTheDocument();
    expect(checkBinding).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /^Add to/ })).toBeNull();
  });

  it("a skill shared with you is flagged BEFORE any click, and one button forks it and adds the folder", async () => {
    renderAdd("shared");
    expect(await screen.findByText(/You can’t change Contract Expert — it’s shared with you, not yours/)).toBeInTheDocument();
    expect(checkBinding).not.toHaveBeenCalled(); // no dead-end "Add" that the server would refuse
    expect(screen.queryByRole("button", { name: /^Add to/ })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Make my copy and add ‘llm’" }));
    expect(forkSkill).toHaveBeenCalledWith("shared");
    expect(addKnowledgeBaseToSkill).toHaveBeenCalledWith("fork-1", "llm-1");
    expect(await screen.findByRole("status")).toHaveTextContent("your copy ‘Contract Expert (Fork)’ can search ‘llm’");
    expect(screen.getByRole("link", { name: "Chat with Contract Expert (Fork)" })).toHaveAttribute(
      "href",
      "/chat/@mark/contract-expert-fork",
    );
  });

  it("a failed fork is shown, not swallowed", async () => {
    forkSkill.mockResolvedValue({ ok: false, message: "Couldn't make your own copy of the skill (HTTP 500)." });
    renderAdd("shared");
    await userEvent.click(await screen.findByRole("button", { name: "Make my copy and add ‘llm’" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't make your own copy");
    expect(addKnowledgeBaseToSkill).not.toHaveBeenCalled();
  });

  it("a skill that searches everyone's own folders needs nothing added", async () => {
    renderAdd("roomy");
    expect(await screen.findByText(/Nothing to add: Research desk already searches your own searchable folders/)).toBeInTheDocument();
    expect(forkSkill).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Make my copy|^Add to/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("a front door that hands searches to a my-folders specialist is covered too — no fork offered", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      res([
        {
          skillId: "door",
          displayName: "Front Door",
          ownerId: "platform",
          managedBy: "template",
          canEdit: false,
          skillMetadata: { tools: [], delegation: { enabled: true, allow: [{ skill: "kb-search" }] } },
        },
        {
          skillId: "uuid-kb",
          slug: "kb-search",
          displayName: "Knowledge Search",
          ownerId: "platform",
          canEdit: false,
          skillMetadata: { tools: ["knowledge_search"], toolConfigs: { knowledge_search: { knowledge_bases: ["lib"], include_my_folders: true } } },
        },
      ]),
    );
    renderAdd("door");
    expect(
      await screen.findByText(/Nothing to add: Front Door already searches your own searchable folders \(through Knowledge Search\), including ‘llm’/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Make my copy/ })).toBeNull();
  });

  it("a delegate that does NOT search my folders still means a fork", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(
      res([
        { skillId: "door", displayName: "Front Door", ownerId: "platform", managedBy: "template", canEdit: false, skillMetadata: { delegation: { enabled: true, allow: ["kb-search"] } } },
        { skillId: "uuid-kb", slug: "kb-search", displayName: "Knowledge Search", ownerId: "platform", skillMetadata: { tools: ["knowledge_search"], toolConfigs: { knowledge_search: { knowledge_bases: ["lib"] } } } },
      ]),
    );
    renderAdd("door");
    expect(await screen.findByRole("button", { name: "Make my copy and add ‘llm’" })).toBeInTheDocument();
  });

  it("an editable built-in skill (admin) explains the deploy reset and still offers a copy", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(res([{ ...skills[1], canEdit: true }]));
    renderAdd("default");
    expect(await screen.findByText(/reset from its template on every deploy/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make my copy and add ‘llm’" })).toBeInTheDocument();
  });

  it("shows a save failure (never silent)", async () => {
    checkBinding.mockResolvedValue({ ok: true, value: { ok: true, results: [{ ref: "llm-1", kbId: "llm-1", ok: true, reason: "" }] } });
    addKnowledgeBaseToSkill.mockResolvedValue({ ok: false, message: "Only the skill owner or an admin can update" });
    renderAdd();
    await userEvent.click(await screen.findByRole("button", { name: "Add to My research" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only the skill owner or an admin can update");
  });

  it("surfaces a skills load failure", async () => {
    vi.mocked(fetchWithAuth).mockResolvedValue(res({}, 500));
    renderAdd();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your skills");
  });
});

describe("SearchAccessPanel", () => {
  it("offers no Public option and saves the chosen access", async () => {
    const onClose = vi.fn();
    render(<SearchAccessPanel kb={kb} folderName="llm" onClose={onClose} />);
    const select = screen.getByRole("combobox");
    expect([...select.querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value)).not.toContain("public");
    await userEvent.selectOptions(select, "domain");
    await userEvent.type(screen.getByPlaceholderText("energy.example"), "sunholo.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(setKnowledgeBaseAccess).toHaveBeenCalledWith("llm-1", { type: "domain", domain: "sunholo.com" }));
    expect(onClose).toHaveBeenCalled();
  });
});
