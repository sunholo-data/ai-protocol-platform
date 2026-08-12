import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

const noop = () => {};

describe("ChatMarkdown", () => {
  it("renders bold text as <strong>", () => {
    const { container } = render(
      <ChatMarkdown content="**bold word**" navigateToBlock={noop} />,
    );
    expect(container.querySelector("strong")).toBeTruthy();
    expect(container.querySelector("strong")?.textContent).toBe("bold word");
  });

  it("renders italic text as <em>", () => {
    const { container } = render(
      <ChatMarkdown content="*italic word*" navigateToBlock={noop} />,
    );
    expect(container.querySelector("em")).toBeTruthy();
  });

  it("renders GFM table as <table>", () => {
    const md = "| a | b |\n|---|---|\n| 1 | 2 |";
    const { container } = render(
      <ChatMarkdown content={md} navigateToBlock={noop} />,
    );
    expect(container.querySelector("table")).toBeTruthy();
    expect(container.querySelectorAll("td")).toHaveLength(2);
  });

  it("renders fenced code block with a code element", () => {
    const md = "```js\nconsole.log('hi');\n```";
    const { container } = render(
      <ChatMarkdown content={md} navigateToBlock={noop} />,
    );
    expect(container.querySelector("code")).toBeTruthy();
  });

  it("renders aitana:// link as InlineCitation chip (button), not plain <a>", () => {
    const md = "[Source](aitana://doc/doc1/block/blk1)";
    const { container } = render(
      <ChatMarkdown content={md} navigateToBlock={noop} />,
    );
    // InlineCitation for aitana:// renders as a <button>
    expect(container.querySelector("button")).toBeTruthy();
    expect(container.querySelector("a")).toBeFalsy();
  });

  it("calls navigateToBlock when an aitana:// chip is clicked", async () => {
    const navigateToBlock = vi.fn();
    const md = "[Source](aitana://doc/doc42/block/blk7)";
    const { container } = render(
      <ChatMarkdown content={md} navigateToBlock={navigateToBlock} />,
    );
    const btn = container.querySelector("button") as HTMLButtonElement;
    btn.click();
    expect(navigateToBlock).toHaveBeenCalledWith("doc42", "blk7");
  });

  it("renders external https link as <a> (not stripped)", () => {
    const md = "[Google](https://google.com)";
    const { container } = render(
      <ChatMarkdown content={md} navigateToBlock={noop} />,
    );
    const anchor = container.querySelector("a");
    expect(anchor).toBeTruthy();
    expect(anchor?.getAttribute("href")).toBe("https://google.com");
  });

  it("strips raw <script> HTML from output (XSS prevention)", () => {
    const { container } = render(
      <ChatMarkdown content="<script>alert(1)</script>" navigateToBlock={noop} />,
    );
    // No executable <script> element — the content may appear as escaped text which is safe
    expect(container.querySelector("script")).toBeFalsy();
  });

  it("strips other raw HTML tags from output", () => {
    const { container } = render(
      <ChatMarkdown content="<img src=x onerror=alert(1)>" navigateToBlock={noop} />,
    );
    expect(container.querySelector("img")).toBeFalsy();
  });

  it("renders plain text unchanged", () => {
    render(<ChatMarkdown content="Hello world" navigateToBlock={noop} />);
    expect(screen.getByText("Hello world")).toBeTruthy();
  });
});
