import { fireEvent, render, screen } from "@testing-library/react";
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
    vi.spyOn(globalThis, "XMLHttpRequest").mockImplementation(function () { return xhrMock as unknown as XMLHttpRequest; });
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

describe("UploadDropZone — folder upload", () => {
  /** Every XHR gets its own instance; responses are chosen by filename. */
  let sent: { fields: Record<string, string>; filename: string }[];
  let respond: (filename: string) => { status: number; body: unknown };

  beforeEach(() => {
    sent = [];
    respond = () => ({ status: 200, body: { docId: "d", unchanged: false } });
    vi.spyOn(globalThis, "XMLHttpRequest").mockImplementation(function () {
      const xhr = {
        open: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { onprogress: null },
        onload: null as null | (() => void),
        onerror: null,
        status: 0,
        responseText: "",
        send(fd: FormData) {
          const fields: Record<string, string> = {};
          let filename = "";
          fd.forEach((v, k) => {
            if (k === "file") filename = (v as File).name;
            else fields[k] = String(v);
          });
          sent.push({ fields, filename });
          const r = respond(filename);
          xhr.status = r.status;
          xhr.responseText = JSON.stringify(r.body);
          setTimeout(() => xhr.onload?.(), 0);
        },
      };
      return xhr as unknown as XMLHttpRequest;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function relFile(relativePath: string): File {
    const f = new File(["x"], relativePath.split("/").pop()!);
    Object.defineProperty(f, "webkitRelativePath", { value: relativePath });
    return f;
  }

  it("renders an 'Upload folder' control with a webkitdirectory input", () => {
    render(<UploadDropZone />);
    expect(screen.getByRole("button", { name: /upload folder/i })).toBeInTheDocument();
    expect(screen.getByTestId("folder-input")).toHaveAttribute("webkitdirectory");
  });

  it("sends folder_path + skip_unchanged (never folder_id), skips junk, and summarises", async () => {
    respond = (name) =>
      name === "same.pdf"
        ? { status: 200, body: { docId: "d2", unchanged: true } }
        : name === "app.exe"
          ? { status: 400, body: { detail: "File type '.exe' is not supported." } }
          : name === "boom.pdf"
            ? { status: 500, body: { detail: "Parser crashed" } }
            : { status: 200, body: { docId: "d1", unchanged: false } };
    const onUploadComplete = vi.fn();
    render(<UploadDropZone folderId="f1" skillId="s1" onUploadComplete={onUploadComplete} />);

    await userEvent.upload(screen.getByTestId("folder-input") as HTMLInputElement, [
      relFile("Reports/2026/q1.pdf"),
      relFile("Reports/top.pdf"),
      relFile("Reports/same.pdf"),
      relFile("Reports/app.exe"),
      relFile("Reports/boom.pdf"),
      relFile("Reports/.DS_Store"),
      relFile("Reports/node_modules/x/index.js"),
    ]);

    await vi.waitFor(() => {
      expect(
        screen.getByText("Folder upload: 2 uploaded, 1 unchanged, 3 skipped (not supported), 1 failed."),
      ).toBeInTheDocument();
    });
    expect(screen.getByText(/Reports\/boom\.pdf: Parser crashed/)).toBeInTheDocument();

    expect(sent).toHaveLength(5); // junk never uploaded
    const q1 = sent.find((s) => s.filename === "q1.pdf")!;
    expect(q1.fields).toEqual({ folder_path: "Reports/2026", skip_unchanged: "true", skill_id: "s1" });
    expect(sent.find((s) => s.filename === "top.pdf")!.fields.folder_path).toBe("Reports");
    for (const s of sent) expect(s.fields).not.toHaveProperty("folder_id");
    // A folder upload must not open every doc as a workbench tab.
    expect(onUploadComplete).not.toHaveBeenCalled();
  });

  it("uploads a dropped folder by walking its entries", async () => {
    render(<UploadDropZone />);
    const fileEntry = (name: string) => ({
      name,
      isFile: true,
      isDirectory: false,
      file: (ok: (f: File) => void) => ok(new File(["x"], name)),
    });
    let read = false;
    const dir = {
      name: "Drop",
      isFile: false,
      isDirectory: true,
      createReader: () => ({
        readEntries: (ok: (e: unknown[]) => void) => {
          ok(read ? [] : [fileEntry("a.pdf"), fileEntry("b.pdf")]);
          read = true;
        },
      }),
    };
    fireEvent.drop(screen.getByText(/drop files here or click to browse/i), {
      dataTransfer: { items: [{ kind: "file", webkitGetAsEntry: () => dir }], files: [] },
    });

    await vi.waitFor(() => {
      expect(screen.getByText("Folder upload: 2 uploaded.")).toBeInTheDocument();
    });
    expect(sent.map((s) => s.fields.folder_path)).toEqual(["Drop", "Drop"]);
  });
});

describe("UploadDropZone — documents-changed notification", () => {
  // Without a Firebase session the doc list reads the API and refetches on
  // this event, so uploads must fire it or new documents never appear.
  let changes: number;
  let unsub: () => void;

  beforeEach(async () => {
    const { subscribeDocumentsChanged } = await import("@/lib/documentEvents");
    changes = 0;
    unsub = subscribeDocumentsChanged(() => {
      changes += 1;
    });
    vi.spyOn(globalThis, "XMLHttpRequest").mockImplementation(function () {
      const xhr = {
        open: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { onprogress: null },
        onload: null as null | (() => void),
        onerror: null,
        status: 200,
        responseText: JSON.stringify({ docId: "d1", unchanged: false }),
        send() {
          setTimeout(() => xhr.onload?.(), 0);
        },
      };
      return xhr as unknown as XMLHttpRequest;
    });
  });

  afterEach(() => {
    unsub();
    vi.restoreAllMocks();
  });

  it("notifies after a single upload finishes", async () => {
    render(<UploadDropZone />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, new File(["x"], "a.pdf", { type: "application/pdf" }));
    await vi.waitFor(() => expect(changes).toBe(1));
  });

  it("notifies once after a folder batch finishes", async () => {
    render(<UploadDropZone />);
    const mk = (p: string) => {
      const f = new File(["x"], p.split("/").pop()!);
      Object.defineProperty(f, "webkitRelativePath", { value: p });
      return f;
    };
    await userEvent.upload(screen.getByTestId("folder-input") as HTMLInputElement, [mk("R/a.pdf"), mk("R/b.pdf")]);
    await vi.waitFor(() => expect(screen.getByText("Folder upload: 2 uploaded.")).toBeInTheDocument());
    expect(changes).toBe(1);
  });
});
