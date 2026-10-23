import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeBasesState } from "@/hooks/useKnowledgeBases";

let kbState: KnowledgeBasesState;
vi.mock("@/hooks/useKnowledgeBases", () => ({ useKnowledgeBases: () => kbState }));
const checkBinding = vi.fn();
vi.mock("@/lib/knowledgeBases", () => ({ checkBinding: (...a: unknown[]) => checkBinding(...a) }));

import { KnowledgeBasePicker } from "../KnowledgeBasePicker";

const llm = { kbId: "llm-1", name: "llm", documentCount: 11, audience: "only its owner", canManage: true };

beforeEach(() => {
  checkBinding.mockReset();
  kbState = { available: true, byId: { "llm-1": llm }, list: [llm] };
});

describe("KnowledgeBasePicker", () => {
  it("offers each user's own folders; ticking it alone is enough to save", async () => {
    const onMine = vi.fn();
    const { rerender } = render(
      <KnowledgeBasePicker value={[]} onChange={vi.fn()} includeMyFolders={false} onIncludeMyFoldersChange={onMine} />,
    );
    const mine = screen.getByRole("checkbox", { name: /Each user’s own searchable folders/ });
    await userEvent.click(mine);
    expect(onMine).toHaveBeenCalledWith(true);
    expect(screen.getByText(/Pick at least one/)).toBeInTheDocument();
    rerender(<KnowledgeBasePicker value={[]} onChange={vi.fn()} includeMyFolders onIncludeMyFoldersChange={onMine} />);
    expect(screen.queryByText(/Pick at least one/)).toBeNull();
  });

  it("renders nothing where the backend has no knowledge bases", () => {
    kbState = { available: false, byId: {}, list: [] };
    const { container } = render(<KnowledgeBasePicker value={[]} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("points to Make searchable when there are none yet", () => {
    kbState = { available: true, byId: {}, list: [] };
    render(<KnowledgeBasePicker value={[]} onChange={vi.fn()} />);
    expect(screen.getByText(/Make searchable/)).toBeInTheDocument();
  });

  it("lists knowledge bases by name with size and audience, and binds by id", async () => {
    const onChange = vi.fn();
    render(<KnowledgeBasePicker value={[]} onChange={onChange} />);
    expect(screen.getByText(/11 documents · searchable by only its owner/)).toBeInTheDocument();
    expect(screen.getByText(/Pick at least one/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox"));
    expect(onChange).toHaveBeenCalledWith(["llm-1"]);
  });

  it("shows the server's refusal before Save, with the fix", async () => {
    checkBinding.mockResolvedValue({
      ok: true,
      value: { ok: false, results: [{ ref: "llm-1", kbId: "llm-1", ok: false, reason: "llm is shared with only its owner, but the skill would reach groups staff" }] },
    });
    render(<KnowledgeBasePicker value={["llm-1"]} onChange={vi.fn()} accessControl={{ type: "tagged", tags: ["staff"] }} />);
    expect(await screen.findByText(/would reach groups staff/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Saving will be refused");
    expect(checkBinding).toHaveBeenCalledWith(["llm-1"], { type: "tagged", tags: ["staff"] }, undefined);
  });

  it("confirms when everyone who can use the skill may search them", async () => {
    checkBinding.mockResolvedValue({ ok: true, value: { ok: true, results: [{ ref: "llm-1", kbId: "llm-1", ok: true, reason: "" }] } });
    render(<KnowledgeBasePicker value={["llm-1"]} onChange={vi.fn()} accessControl={{ type: "private" }} />);
    await waitFor(() => expect(screen.getByText(/Everyone who can use this skill may search these/)).toBeInTheDocument());
  });

  it("keeps a bound knowledge base the author can't see, visibly", async () => {
    checkBinding.mockResolvedValue({ ok: true, value: { ok: false, results: [{ ref: "secret", ok: false, reason: "Unknown knowledge base 'secret'." }] } });
    render(<KnowledgeBasePicker value={["secret"]} onChange={vi.fn()} />);
    expect(await screen.findByText("Unknown knowledge base 'secret'.")).toBeInTheDocument();
  });
});
