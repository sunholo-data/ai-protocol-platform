import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AssistantIntroBubble } from "../AssistantIntroBubble";

// Avatar and markdown components have their own hosts of dependencies; stub
// to focus on AssistantIntroBubble's own contract.
vi.mock("@/components/chat/BrandAvatar", () => ({
  BrandAvatar: () => <span data-testid="brand-avatar" />,
}));
vi.mock("@/components/chat/ChatMarkdown", () => ({
  ChatMarkdown: ({ content }: { content: string }) => (
    <div data-testid="markdown">{content}</div>
  ),
}));

describe("AssistantIntroBubble", () => {
  it("renders the content via ChatMarkdown + 'not stored' caption", () => {
    render(<AssistantIntroBubble content="PPA, PtX, BESS — what would you like to analyse?" />);
    expect(screen.getByTestId("markdown")).toHaveTextContent(
      "PPA, PtX, BESS — what would you like to analyse?",
    );
    expect(screen.getByText(/Intro · not stored/i)).toBeInTheDocument();
  });

  it("defaults the skill name to 'Assistant' when skillName prop omitted", () => {
    render(<AssistantIntroBubble content="hello" />);
    expect(screen.getByText("Assistant")).toBeInTheDocument();
  });

  it("uses the provided skillName in the headline", () => {
    render(<AssistantIntroBubble content="hello" skillName="Contract Expert" />);
    expect(screen.getByText("Contract Expert")).toBeInTheDocument();
  });
});
