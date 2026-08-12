import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InlineImage } from "@/components/chat/media/InlineImage";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

// Radix Dialog uses portals — need to query document.body, not container
describe("InlineImage", () => {
  it("renders an <img> with the given src and alt", () => {
    render(<InlineImage src="https://example.com/photo.png" alt="A photo" />);
    const img = screen.getByRole("img", { name: "A photo" });
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("https://example.com/photo.png");
  });

  it("has lazy loading attribute", () => {
    render(<InlineImage src="https://example.com/photo.png" alt="photo" />);
    const img = screen.getByRole("img");
    expect(img.getAttribute("loading")).toBe("lazy");
  });

  it("shows broken image fallback text after error", () => {
    render(<InlineImage src="https://example.com/broken.png" alt="missing" />);
    const img = screen.getByRole("img", { name: "missing" });
    fireEvent.error(img);
    expect(screen.queryByRole("img", { name: "missing" })).toBeFalsy();
    expect(screen.getByText("missing")).toBeTruthy();
  });

  it("shows 'image unavailable' fallback when no alt provided", () => {
    const { container } = render(<InlineImage src="https://example.com/broken.png" />);
    const img = container.querySelector("img")!;
    fireEvent.error(img);
    expect(screen.getByText("image unavailable")).toBeTruthy();
  });
});

describe("ChatMarkdown image rendering", () => {
  const noop = () => {};

  it("renders markdown image syntax as InlineImage (<img> with lazy loading)", () => {
    const md = "![alt text](https://example.com/diagram.png)";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    const img = container.querySelector("img");
    expect(img).toBeTruthy();
    expect(img?.getAttribute("loading")).toBe("lazy");
    expect(img?.getAttribute("src")).toBe("https://example.com/diagram.png");
  });

  it("does not render images with unsafe URLs (javascript: scheme)", () => {
    // urlTransform blocks non-http(s) schemes — img src becomes "#"
    const md = "![xss](javascript:alert(1))";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    const img = container.querySelector("img");
    // Either no img or img with src="#"
    if (img) {
      expect(img.getAttribute("src")).toBe("#");
    }
  });

  it("renders broken image link as error fallback after onError", () => {
    const md = "![broken](https://example.com/gone.png)";
    const { container } = render(<ChatMarkdown content={md} navigateToBlock={noop} />);
    const img = container.querySelector("img");
    expect(img).toBeTruthy();
    fireEvent.error(img!);
    expect(container.querySelector("img")).toBeFalsy();
    expect(container.textContent).toContain("broken");
  });
});
