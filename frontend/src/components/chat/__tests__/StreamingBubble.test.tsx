import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StreamingBubble } from "../StreamingBubble";
import type { SkillMessage } from "@/hooks/useSkillAgent";
import { BRANDING } from "@/lib/branding";

function makeMsg(content: string): SkillMessage {
  return { id: "msg-1", role: "assistant", content };
}

describe("StreamingBubble", () => {
  it("renders partial message content", () => {
    render(<StreamingBubble message={makeMsg("Hello wor")} skillId="test-skill" />);
    expect(screen.getByText(/hello wor/i)).toBeInTheDocument();
  });

  it("shows a blinking cursor", () => {
    const { container } = render(
      <StreamingBubble message={makeMsg("typing...")} skillId="test-skill" />,
    );
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows the skill name", () => {
    render(<StreamingBubble message={makeMsg("hi")} skillId="my-skill" />);
    expect(screen.getByText("my-skill")).toBeInTheDocument();
  });

  it("renders the bot avatar with branded alt text", () => {
    render(<StreamingBubble message={makeMsg("hi")} skillId="s" />);
    expect(screen.getByRole("img", { name: BRANDING.appName })).toBeInTheDocument();
  });

  it("applies orange left border class", () => {
    const { container } = render(
      <StreamingBubble message={makeMsg("hi")} skillId="s" />,
    );
    expect(container.querySelector(".border-orange-400")).toBeInTheDocument();
  });
});
