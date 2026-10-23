import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));

import { fetchWithAuth } from "@/lib/apiClient";
import { __resetMyInstructionsStore, useMyInstructions } from "@/hooks/useMyInstructions";
import { ContextBanner } from "@/components/chat/ContextBanner";
import { YOUR_INSTRUCTIONS_PLACEHOLDER, YourInstructionsCard } from "../YourInstructionsCard";

const mockFetch = vi.mocked(fetchWithAuth);

function res(status: number, body?: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body ?? {} } as unknown as Response;
}

const API = "/api/proxy/api/me/instructions";

beforeEach(() => {
  __resetMyInstructionsStore();
  mockFetch.mockReset();
});
afterEach(() => __resetMyInstructionsStore());

/** What ChatShell does with the store: banner on when instructions are set. */
function BannerFromStore() {
  const { instructions, status } = useMyInstructions();
  return <ContextBanner context={null} instructionsOn={status === "ready" && instructions.trim().length > 0} />;
}

describe("Your instructions card", () => {
  it("loads the saved text, with the example as placeholder", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "Answer in Spanish." }));
    render(<YourInstructionsCard />);
    const box = await screen.findByRole("textbox", { name: "Your instructions" });
    expect(box).toHaveValue("Answer in Spanish.");
    expect(box).toHaveAttribute("placeholder", YOUR_INSTRUCTIONS_PLACEHOLDER);
    expect(mockFetch.mock.calls[0][0]).toBe(API);
  });

  it("a load failure is visible, with Retry", async () => {
    mockFetch.mockResolvedValueOnce(res(500));
    render(<YourInstructionsCard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your instructions.");
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "" }));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("textbox", { name: "Your instructions" })).toBeInTheDocument();
  });

  it("saves on blur, says Saved, and the chat banner turns on at once", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "" }));
    render(
      <>
        <BannerFromStore />
        <YourInstructionsCard />
      </>,
    );
    const box = await screen.findByRole("textbox", { name: "Your instructions" });
    expect(screen.queryByTestId("instructions-on")).toBeNull();
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "Answer in Spanish." }));
    await user.type(box, "Answer in Spanish.");
    fireEvent.blur(box);
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    const [url, init] = mockFetch.mock.calls[1];
    expect(url).toBe(API);
    expect(init).toMatchObject({ method: "PUT" });
    expect(JSON.parse(String(init?.body))).toEqual({ instructions: "Answer in Spanish." });
    expect(screen.getByTestId("instructions-on")).toHaveTextContent("Your instructions on");
    expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute("href", "/settings/instructions");
  });

  it("a failed save is shown with Retry, and Retry saves again", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "" }));
    render(<YourInstructionsCard />);
    const box = await screen.findByRole("textbox", { name: "Your instructions" });
    mockFetch.mockResolvedValueOnce(res(503));
    await user.type(box, "Be brief.");
    fireEvent.blur(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save your instructions (HTTP 503).");
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "Be brief." }));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("too long is refused before sending, with the count", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "" }));
    render(<YourInstructionsCard />);
    const box = await screen.findByRole("textbox", { name: "Your instructions" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "x".repeat(4001) } });
    fireEvent.blur(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("At most 4,000 characters — this is 4,001.");
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("leaving it unchanged saves nothing", async () => {
    mockFetch.mockResolvedValueOnce(res(200, { instructions: "Same" }));
    render(<YourInstructionsCard />);
    const box = await screen.findByRole("textbox", { name: "Your instructions" });
    fireEvent.focus(box);
    fireEvent.blur(box);
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
  });
});

describe("ContextBanner", () => {
  it("renders nothing when there's neither a folder nor instructions", () => {
    const { container } = render(<ContextBanner context={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows both a folder context and instructions together", () => {
    render(<ContextBanner context={{ folderName: "PPAs", docCount: 2 }} instructionsOn />);
    expect(screen.getByText("PPAs")).toBeInTheDocument();
    expect(screen.getByTestId("instructions-on")).toBeInTheDocument();
  });
});
