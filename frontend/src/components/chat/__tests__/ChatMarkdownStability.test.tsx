/**
 * ChatMarkdown must not remount its rendered subtree (v6.19.0, AIPLA #44).
 *
 * react-markdown treats each `components` override as a React element TYPE.
 * A fresh `components` object identity per render therefore causes a REMOUNT
 * (teardown + rebuild of real DOM nodes), not a re-render — for every message,
 * on any parent re-render. It showed up as continuous SVG-diagram flicker, but
 * the cost is generic.
 *
 * The assertion has to be **DOM node identity**, not rendered output. A
 * "renders the right markdown" test passes identically against the broken
 * version, which is why this bug survived a full suite.
 */

import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

describe("ChatMarkdown render stability", () => {
  it("keeps the same DOM nodes across a re-render with unchanged props", () => {
    const navigateToBlock = vi.fn();
    const content = "# Heading\n\nSome **bold** text.";

    const { container, rerender } = render(
      <ChatMarkdown content={content} navigateToBlock={navigateToBlock} />,
    );
    const headingBefore = container.querySelector("h1");
    expect(headingBefore).not.toBeNull();

    // A parent re-rendering for an unrelated reason.
    rerender(<ChatMarkdown content={content} navigateToBlock={navigateToBlock} />);

    const headingAfter = container.querySelector("h1");
    expect(headingAfter).toBe(headingBefore); // same node object => no remount
  });

  it("survives many re-renders without rebuilding the tree", () => {
    const navigateToBlock = vi.fn();
    const content = "Paragraph with `code`.";

    const { container, rerender } = render(
      <ChatMarkdown content={content} navigateToBlock={navigateToBlock} />,
    );
    const codeBefore = container.querySelector("code");

    for (let i = 0; i < 5; i++) {
      rerender(<ChatMarkdown content={content} navigateToBlock={navigateToBlock} />);
    }

    expect(container.querySelector("code")).toBe(codeBefore);
  });

  it("still re-renders correctly when the content actually changes", () => {
    const navigateToBlock = vi.fn();

    const { container, rerender } = render(
      <ChatMarkdown content="First" navigateToBlock={navigateToBlock} />,
    );
    expect(container.textContent).toContain("First");

    rerender(<ChatMarkdown content="Second" navigateToBlock={navigateToBlock} />);

    expect(container.textContent).toContain("Second");
    expect(container.textContent).not.toContain("First");
  });
});
