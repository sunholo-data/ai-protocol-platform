import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { InlineImage } from "@/components/chat/media/InlineImage";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
import { __clearAuthImageCache, authImagePath, isChatMediaPath } from "@/lib/authImage";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
import { fetchWithAuth } from "@/lib/apiClient";
const mockFetch = vi.mocked(fetchWithAuth);

function fileResponse(type = "image/png"): Response {
  return {
    ok: true,
    blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type }),
  } as unknown as Response;
}
const pngResponse = () => fileResponse();

beforeEach(() => {
  __clearAuthImageCache();
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(pngResponse());
  if (!("createObjectURL" in URL)) {
    // @ts-expect-error test shim
    URL.createObjectURL = vi.fn(() => "blob:mock");
    // @ts-expect-error test shim
    URL.revokeObjectURL = vi.fn();
  } else {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  }
});

const DOC_IMAGE = "/api/documents/doc-1/thumbnail?width=1200";

describe("chat images from authenticated backend routes", () => {
  it("fetches a backend image through the proxy with credentials and shows a blob", async () => {
    render(<InlineImage src={DOC_IMAGE} alt="pics/living-room-01.jpg" />);
    // A placeholder first, never an anonymous <img> request that 401s.
    expect(screen.getByLabelText("Loading pics/living-room-01.jpg")).toBeTruthy();
    const img = await screen.findByRole("img", { name: "pics/living-room-01.jpg" });
    expect(img.getAttribute("src")).toBe("blob:mock");
    expect(mockFetch).toHaveBeenCalledWith(`/api/proxy${DOC_IMAGE}`);
  });

  it("says so when the image cannot be fetched", async () => {
    mockFetch.mockResolvedValue({ ok: false, status: 403 } as Response);
    render(<InlineImage src={DOC_IMAGE} alt="kitchen" />);
    await waitFor(() => expect(screen.getByText("kitchen")).toBeTruthy());
    expect(screen.queryByRole("img", { name: "kitchen" })).toBeFalsy();
  });

  it("loads a remote image directly, without the proxy", () => {
    render(<InlineImage src="https://example.com/a.png" alt="remote" />);
    expect(screen.getByRole("img", { name: "remote" }).getAttribute("src")).toBe("https://example.com/a.png");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("renders a show_media markdown line from a reply", async () => {
    const { container } = render(
      <ChatMarkdown content={`Here it is:\n\n![pics/living-room-01.jpg](${DOC_IMAGE})`} navigateToBlock={() => {}} />,
    );
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:mock"));
    expect(mockFetch).toHaveBeenCalledWith(`/api/proxy${DOC_IMAGE}`);
  });

  it("plays a video document as a video, not an image", async () => {
    mockFetch.mockResolvedValue(fileResponse("video/mp4"));
    const { container } = render(<InlineImage src="/api/documents/clip/preview" alt="walkthrough.mp4" />);
    await waitFor(() => expect(container.querySelector("video")).toBeTruthy());
    const video = container.querySelector("video")!;
    expect(video.getAttribute("src")).toBe("blob:mock");
    expect(video.hasAttribute("controls")).toBe(true);
    expect(container.querySelector("img")).toBeFalsy();
  });

  it("plays audio with its caption", async () => {
    mockFetch.mockResolvedValue(fileResponse("audio/wav"));
    const { container } = render(
      <ChatMarkdown content="![narration.wav](/api/media/narration_1.wav)" navigateToBlock={() => {}} />,
    );
    await waitFor(() => expect(container.querySelector("audio")).toBeTruthy());
    expect(container.querySelector("audio")!.getAttribute("src")).toBe("blob:mock");
    expect(container.textContent).toContain("narration.wav");
    expect(mockFetch).toHaveBeenCalledWith("/api/proxy/api/media/narration_1.wav");
  });

  it("does not let a reply point an image at any other backend route", () => {
    const { container } = render(
      <ChatMarkdown content="![x](/api/admin/users)" navigateToBlock={() => {}} />,
    );
    const img = container.querySelector("img");
    if (img) expect(img.getAttribute("src")).toBe("#");
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("authImage paths", () => {
  it("maps backend paths onto the proxy and leaves the rest alone", () => {
    expect(authImagePath("/api/browser/artefacts/a.png")).toBe("/api/proxy/api/browser/artefacts/a.png");
    expect(authImagePath("/api/proxy/api/browser/artefacts/a.png")).toBe("/api/proxy/api/browser/artefacts/a.png");
    expect(authImagePath("https://example.com/a.png")).toBeNull();
    expect(authImagePath("blob:abc")).toBeNull();
  });

  it("allows exactly the show_media routes in a reply", () => {
    expect(isChatMediaPath(DOC_IMAGE)).toBe(true);
    expect(isChatMediaPath("/api/buckets/example-bucket/thumbnail?object=a%2Fb.jpg&width=1200")).toBe(true);
    expect(isChatMediaPath("/api/browser/artefacts/browser_screenshot_example.com_1.png")).toBe(true);
    expect(isChatMediaPath("/api/documents/doc-1/preview")).toBe(true);
    expect(isChatMediaPath("/api/buckets/example-bucket/preview?object=a%2Ftour.mp4")).toBe(true);
    expect(isChatMediaPath("/api/media/narration_20261110_abcd.wav")).toBe(true);
    expect(isChatMediaPath("/api/documents/doc-1")).toBe(false);
    expect(isChatMediaPath("/api/documents/doc-1/download")).toBe(false);
    expect(isChatMediaPath("/api/documents/../admin/thumbnail")).toBe(false);
    expect(isChatMediaPath("/api/documents/%2e%2e/thumbnail")).toBe(false);
    expect(isChatMediaPath("/api/admin/users")).toBe(false);
  });
});
