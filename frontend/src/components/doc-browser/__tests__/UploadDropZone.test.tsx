import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadDropZone } from "../UploadDropZone";

// Mock Firebase ID token — not needed for upload logic
vi.mock("@/lib/firebase", () => ({
  getIdToken: vi.fn().mockResolvedValue("mock-token"),
}));

describe("UploadDropZone", () => {
  let xhrMock: {
    open: ReturnType<typeof vi.fn>;
    setRequestHeader: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
    upload: { onprogress: ((e: ProgressEvent) => void) | null };
    onload: (() => void) | null;
    onerror: (() => void) | null;
    status: number;
    responseText: string;
  };

  beforeEach(() => {
    xhrMock = {
      open: vi.fn(),
      setRequestHeader: vi.fn(),
      send: vi.fn(),
      upload: { onprogress: null },
      onload: null,
      onerror: null,
      status: 200,
      responseText: JSON.stringify({ docId: "new-doc", status: "parsed", blocksCount: 5 }),
    };
    vi.spyOn(globalThis, "XMLHttpRequest").mockImplementation(() => xhrMock as unknown as XMLHttpRequest);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the drop zone with placeholder text", () => {
    render(<UploadDropZone />);
    expect(screen.getByText(/drop files here or click to browse/i)).toBeInTheDocument();
  });

  it("calls XMLHttpRequest.open with upload endpoint on file selection", async () => {
    render(<UploadDropZone folderId="f1" />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["content"], "report.docx", { type: "application/octet-stream" });

    await userEvent.upload(input, file);

    expect(xhrMock.open).toHaveBeenCalledWith("POST", "/api/proxy/api/documents/upload");
  });

  it("calls onUploadComplete with docId when upload succeeds", async () => {
    const onUploadComplete = vi.fn();
    render(<UploadDropZone onUploadComplete={onUploadComplete} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "test.pdf", { type: "application/pdf" });

    await userEvent.upload(input, file);

    // Simulate XHR load with status 200
    xhrMock.status = 200;
    xhrMock.onload?.();

    await vi.waitFor(() => {
      expect(onUploadComplete).toHaveBeenCalledWith("new-doc", "test.pdf");
    });
  });

  it("surfaces the backend's structured reason on a 403 (NEVER-SILENT)", async () => {
    render(<UploadDropZone />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "contract.pdf", { type: "application/pdf" });

    await userEvent.upload(input, file);

    // Fail-closed tenant denial: FastAPI detail is a structured {code, message}.
    xhrMock.status = 403;
    xhrMock.responseText = JSON.stringify({
      detail: {
        code: "TENANT_NOT_PROVISIONED",
        message: "Document upload isn't available for your account.",
      },
    });
    xhrMock.onload?.();

    await vi.waitFor(() => {
      expect(screen.getByText(/Document upload isn't available for your account\./)).toBeInTheDocument();
    });
  });

  it("falls back to a generic message when a non-JSON error body is returned", async () => {
    render(<UploadDropZone />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "boom.pdf", { type: "application/pdf" });

    await userEvent.upload(input, file);

    xhrMock.status = 500;
    xhrMock.responseText = "<html>Internal Server Error</html>";
    xhrMock.onload?.();

    await vi.waitFor(() => {
      expect(screen.getByText(/Upload failed \(500\)/)).toBeInTheDocument();
    });
  });
});
