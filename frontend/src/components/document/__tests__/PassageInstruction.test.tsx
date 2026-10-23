// v6.34.0 DOC-WORKBENCH P4b — highlight a passage, say what to change.
//
// Rewritten 2026-09-01 after the first real use on test. The component used to
// compose a chat message and let the agent regenerate the whole document; it now
// calls `/revise-passage`, which sends ONLY the passage to the model and splices
// the result back. What is pinned here is that contract plus the failure paths,
// because the typed backend errors are the actionable ones.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { PassageInstruction } from "../PassageInstruction";
import { fetchWithAuth } from "@/lib/apiClient";

vi.mock("@/lib/apiClient", () => ({ fetchWithAuth: vi.fn() }));
const mockFetch = vi.mocked(fetchWithAuth);

function reply(status: number, body: unknown = {}) {
  return { ok: status < 400, status, json: async () => body } as unknown as Response;
}

/** An SSE response whose frames arrive as one chunk each, like the real route. */
function sseReply(frames: unknown[], status = 200) {
  const encoder = new TextEncoder();
  const queue = frames.map((f) => encoder.encode(`data: ${JSON.stringify(f)}\n\n`));
  let i = 0;
  return {
    ok: status < 400,
    status,
    json: async () => ({}),
    body: {
      getReader: () => ({
        read: async () =>
          i < queue.length ? { done: false, value: queue[i++] } : { done: true, value: undefined },
      }),
    },
  } as unknown as Response;
}

/** The ordinary success: the backend's own labels, then a terminal done frame. */
function sseSuccess() {
  return sseReply([
    { type: "progress", label: "Preparing the document…" },
    { type: "progress", label: "Rewriting the passage…" },
    { type: "done", document: { docId: "doc-1" } },
  ]);
}

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue(sseSuccess());
});

function Harness() {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={ref} data-testid="body">
        <p>The term is ten years and may be extended.</p>
      </div>
      <PassageInstruction docId="doc-1" containerRef={ref} />
    </div>
  );
}

/** Simulate selecting text inside the watched container. */
function selectInBody(text: string) {
  const body = screen.getByTestId("body");
  const node = body.querySelector("p")!.firstChild!;
  const range = document.createRange();
  range.setStart(node, 0);
  range.setEnd(node, text.length);
  // jsdom's Range has no getBoundingClientRect; the component guards against
  // that, but stub it so the positioning path is the one under test.
  (range as unknown as { getBoundingClientRect: () => DOMRect }).getBoundingClientRect = () =>
    ({ top: 40, bottom: 60, left: 20, right: 200, width: 180, height: 20 }) as DOMRect;
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  body.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
}

const dialog = () => screen.queryByRole("dialog", { name: /revise selected passage/i });

async function openOn(text: string) {
  render(<Harness />);
  selectInBody(text);
  return screen.findByRole("dialog", { name: /revise selected passage/i });
}

describe("PassageInstruction", () => {
  it("stays out of the way until something is selected", () => {
    render(<Harness />);
    expect(dialog()).not.toBeInTheDocument();
  });

  it("opens on a selection, showing what was picked", async () => {
    const d = await openOn("The term is ten years");
    expect(d).toHaveTextContent(/The term is ten years/);
  });

  it("ignores a stray click that selects almost nothing", async () => {
    render(<Harness />);
    selectInBody("Th");
    expect(dialog()).not.toBeInTheDocument();
  });

  it("sends the passage and the instruction to the splice endpoint", async () => {
    await openOn("The term is ten years");

    await userEvent.type(screen.getByLabelText(/what should change/i), "make it more concise");
    await userEvent.click(screen.getByRole("button", { name: "Rewrite" }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/proxy/api/documents/doc-1/revise-passage/stream");
    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent.anchorText).toBe("The term is ten years");
    expect(sent.instruction).toBe("make it more concise");
  });

  it("does NOT send the whole document — only the passage", async () => {
    await openOn("The term is ten years");

    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const sent = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(Object.keys(sent).sort()).toEqual(["anchorText", "instruction"]);
  });

  it("submits on Enter", async () => {
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
  });

  it("will not send an empty instruction", async () => {
    await openOn("The term is ten years");
    expect(screen.getByRole("button", { name: "Rewrite" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/what should change/i), "   {Enter}");
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows a working state rather than going quiet", async () => {
    let release: (r: Response) => void = () => {};
    mockFetch.mockReturnValue(new Promise<Response>((r) => (release = r)));

    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByText(/Rewriting just this passage/i)).toBeInTheDocument();
    release(sseSuccess());
  });

  it("shows the backend's own progress labels, not an invented sequence", async () => {
    // Held open after the progress frame, so the label is observable rather
    // than flashing past — which is the point of streaming it at all.
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const encoder = new TextEncoder();
    const frames = [
      encoder.encode(`data: ${JSON.stringify({ type: "progress", label: "Rewriting the passage…" })}\n\n`),
      encoder.encode(`data: ${JSON.stringify({ type: "done", document: {} })}\n\n`),
    ];
    let i = 0;
    mockFetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
      body: {
        getReader: () => ({
          read: async () => {
            if (i === 1) await gate;
            return i < frames.length ? { done: false, value: frames[i++] } : { done: true, value: undefined };
          },
        }),
      },
    } as unknown as Response);

    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    // The label comes from the stream — the same one the backend emits.
    expect(await screen.findByText(/Rewriting the passage/i)).toBeInTheDocument();
    release();
  });

  it("treats a stream that stops without a terminal frame as a failure", async () => {
    // The silence this endpoint exists to remove: frames, then nothing. It must
    // NOT resolve quietly as though the edit had landed.
    mockFetch.mockResolvedValue(sseReply([{ type: "progress", label: "Rewriting the passage…" }]));

    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(/stopped without finishing/i);
    expect(screen.queryByText(/Updated/i)).not.toBeInTheDocument();
  });

  it("surfaces an error frame from the stream", async () => {
    mockFetch.mockResolvedValue(
      sseReply([
        { type: "progress", label: "Preparing the document…" },
        { type: "error", status: 422, code: "PASSAGE_NOT_FOUND", message: "That passage could not be located." },
      ]),
    );

    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be located/);
  });

  it("confirms when the change lands", async () => {
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByText(/Updated/i)).toBeInTheDocument();
  });

  it("shows the backend's typed error verbatim", async () => {
    mockFetch.mockResolvedValue(
      reply(422, {
        detail: { code: "PASSAGE_NOT_FOUND", message: "That passage could not be located — try selecting it again." },
      }),
    );
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be located/);
  });

  it("falls back to a status when there is no typed detail", async () => {
    mockFetch.mockResolvedValue(reply(500));
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("500");
  });

  it("surfaces a network failure", async () => {
    mockFetch.mockRejectedValue(new Error("offline"));
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  });

  it("stays open after a failure so the instruction can be retried", async () => {
    mockFetch.mockResolvedValue(reply(500));
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Enter}");

    await screen.findByRole("alert");
    expect(dialog()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rewrite" })).toBeEnabled();
  });

  it("dismisses on Escape without sending", async () => {
    await openOn("The term is ten years");
    await userEvent.type(screen.getByLabelText(/what should change/i), "shorter{Escape}");

    expect(mockFetch).not.toHaveBeenCalled();
    expect(dialog()).not.toBeInTheDocument();
  });

  it("url-encodes the doc id", async () => {
    render(
      <div>
        <PassageInstructionHarnessWithId />
      </div>,
    );
    selectInBody("The term is ten years");
    await screen.findByRole("dialog", { name: /revise selected passage/i });
    await userEvent.type(screen.getByLabelText(/what should change/i), "x{Enter}");

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(mockFetch.mock.calls[0][0]).toBe("/api/proxy/api/documents/a%2Fb/revise-passage/stream");
  });
});

function PassageInstructionHarnessWithId() {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div>
      <div ref={ref} data-testid="body">
        <p>The term is ten years and may be extended.</p>
      </div>
      <PassageInstruction docId="a/b" containerRef={ref} />
    </div>
  );
}
