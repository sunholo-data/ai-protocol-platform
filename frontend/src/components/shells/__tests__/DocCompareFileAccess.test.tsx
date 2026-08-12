import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { WelcomeConfig } from "@/types/skill";

let surfacePresent = false;
let welcome: WelcomeConfig | null = null;
const importByReference = vi.fn();
const sendMessage = vi.fn();

vi.mock("@/hooks/useSkillAgent", () => ({
  useSkillAgent: () => ({ sessionId: "s1", sendMessage, isLoading: false }),
}));
vi.mock("@/hooks/useSkillMeta", () => ({
  useSkillMeta: () => ({ welcome, initialMessage: "", displayName: "Compare", mcpServerIds: [] }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/providers/SurfaceRegistry", () => ({
  SurfaceRegistryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useClearSurfacesOnSessionChange: () => undefined,
  useSurfaceState: () => (surfacePresent ? { surface: { id: "workspace" } } : null),
}));
vi.mock("@/lib/importByReference", () => ({
  importByReference: (...a: unknown[]) => importByReference(...(a as [])),
  isImportError: () => false,
}));
vi.mock("@/components/shells/DrawerChatPane", () => ({ DrawerChatPane: () => <div data-testid="drawer" /> }));
vi.mock("@/components/protocols/A2UISurfaceMount", () => ({
  A2UISurfaceMount: () => <div data-testid="a2ui-surface" />,
}));
vi.mock("@/components/chat/SkillExamplesPicker", () => ({
  SkillExamplesPicker: ({ examples, onPickExample }: { examples: { bucket: string; object: string }[]; onPickExample: (e: unknown) => void }) => (
    <button data-testid="pick-example" onClick={() => onPickExample(examples[0])}>
      examples
    </button>
  ),
}));
vi.mock("@/components/doc-browser/GCSFileBrowser", () => ({
  GCSFileBrowser: ({ bucket, onPick }: { bucket: string; onPick: (b: string, o: string, l: string) => void }) => (
    <button data-testid="pick-bucket" onClick={() => onPick(bucket, "longform/contract.pdf", "contract.pdf")}>
      browser
    </button>
  ),
}));

import { DocCompareShell } from "@/components/shells/DocCompareShell";

const props = {
  skillId: "skill-1",
  pathPrefix: "/chat/@aitana-platform/one-doc-compare",
  user: { uid: "u1", displayName: "Mark", email: "m@x.com", photoURL: null } as never,
};
const fullWelcome: WelcomeConfig = {
  introMessage: "Pick two PPAs.",
  exampleDocuments: [{ bucket: "demo-bkt", object: "ppa/a.pdf", label: "A" }],
  bucketBrowser: { bucket: "demo-bkt", rootPath: "PPAs/longform/", label: "Contracts library" },
};

describe("DocCompareShell file access", () => {
  beforeEach(() => {
    surfacePresent = false;
    welcome = null;
    importByReference.mockReset();
    // distinct id per object so two different picks select two docs
    importByReference.mockImplementation(async (_b: string, o: string) => ({ doc: { id: o } }));
    sendMessage.mockReset();
  });

  it("renders the examples picker + bucket library in the empty state when welcome is set", () => {
    welcome = fullWelcome;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    expect(screen.getByTestId("doc-compare-files")).not.toBeNull();
    expect(screen.getByTestId("pick-example")).not.toBeNull();
    expect(screen.getByTestId("pick-bucket")).not.toBeNull();
  });

  it("imports a picked example via import-by-reference", async () => {
    welcome = fullWelcome;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    fireEvent.click(screen.getByTestId("pick-example"));
    await waitFor(() => expect(importByReference).toHaveBeenCalledWith("demo-bkt", "ppa/a.pdf", "skill-1"));
  });

  it("imports a picked bucket file via import-by-reference", async () => {
    welcome = fullWelcome;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    fireEvent.click(screen.getByTestId("pick-bucket"));
    await waitFor(() =>
      expect(importByReference).toHaveBeenCalledWith("demo-bkt", "longform/contract.pdf", "skill-1"),
    );
  });

  it("hides the file panel and shows the comparison once a workspace surface is present", () => {
    welcome = fullWelcome;
    surfacePresent = true;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    expect(screen.getByTestId("doc-compare-canvas")).not.toBeNull();
    expect(screen.queryByTestId("doc-compare-files")).toBeNull();
  });

  it("falls back to the bare prompt when the skill has no welcome config", () => {
    welcome = null;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    expect(screen.getByTestId("doc-compare-empty")).not.toBeNull();
    expect(screen.queryByTestId("pick-bucket")).toBeNull();
  });

  it("runs the comparison via document_ids (artifact path) after two distinct picks", async () => {
    welcome = fullWelcome;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    fireEvent.click(screen.getByTestId("pick-example"));
    await waitFor(() => expect(importByReference).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("pick-bucket"));
    await waitFor(() => expect(screen.getByTestId("doc-compare-run")).not.toBeNull());

    fireEvent.click(screen.getByTestId("doc-compare-run"));
    expect(sendMessage).toHaveBeenCalledTimes(1);
    // Only document ids travel — no eager-inline (no resumedSession). The
    // loader turns these into artifacts the compare tool reads selectively.
    expect(sendMessage.mock.calls[0][1]).toEqual({
      documentIds: ["ppa/a.pdf", "longform/contract.pdf"],
    });
  });

  it("dedupes identical picks — selecting the same doc twice shows no compare button", async () => {
    welcome = fullWelcome;
    render(<DocCompareShell {...props} shell={{ mode: "doc-compare" }} />);
    fireEvent.click(screen.getByTestId("pick-example"));
    await waitFor(() => expect(screen.getByTestId("doc-compare-selected")).not.toBeNull());
    fireEvent.click(screen.getByTestId("pick-example"));
    await waitFor(() => expect(importByReference).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("doc-compare-run")).toBeNull();
  });
});
