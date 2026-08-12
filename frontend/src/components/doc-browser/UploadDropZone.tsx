"use client";

import { useRef, useState } from "react";
import { getIdToken } from "@/lib/firebase";

interface UploadProgress {
  filename: string;
  pct: number;
  status: "uploading" | "done" | "error";
  error?: string;
}

interface UploadDropZoneProps {
  folderId?: string;
  skillId?: string;
  onUploadComplete?: (docId: string, filename: string) => void;
}

const MAX_CONCURRENT = 4;

export function UploadDropZone({ folderId = "", skillId = "", onUploadComplete }: UploadDropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [progress, setProgress] = useState<UploadProgress[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const semaphoreRef = useRef(0);
  const queueRef = useRef<File[]>([]);

  function updateProgress(filename: string, patch: Partial<UploadProgress>) {
    setProgress((prev) => {
      const idx = prev.findIndex((p) => p.filename === filename);
      if (idx === -1) return [...prev, { filename, pct: 0, status: "uploading", ...patch }];
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      return next;
    });
  }

  async function uploadFile(file: File) {
    updateProgress(file.name, { status: "uploading", pct: 0 });

    const token = await getIdToken();
    const formData = new FormData();
    formData.append("file", file);
    if (folderId) formData.append("folder_id", folderId);
    if (skillId) formData.append("skill_id", skillId);

    await new Promise<void>((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/proxy/api/documents/upload");
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          updateProgress(file.name, { pct: Math.round((e.loaded / e.total) * 100) });
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 400) {
          // Surface the backend's specific reason (NEVER-SILENT) rather than a
          // bare status. FastAPI `detail` is either a string or a structured
          // {code, message} (e.g. TENANT_NOT_PROVISIONED on a fail-closed
          // tenant) — show whichever is present, falling back to the status.
          let message = `Upload failed (${xhr.status})`;
          try {
            const detail = (JSON.parse(xhr.responseText) as { detail?: unknown }).detail;
            if (typeof detail === "string" && detail.trim()) {
              message = detail;
            } else if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
              message = (detail as { message: string }).message;
            }
          } catch {
            // non-JSON body — keep the generic status message
          }
          updateProgress(file.name, { status: "error", pct: 0, error: message });
        } else {
          updateProgress(file.name, { status: "done", pct: 100 });
          try {
            const data = JSON.parse(xhr.responseText) as { docId?: string };
            if (data.docId) onUploadComplete?.(data.docId, file.name);
          } catch {
            // ignore parse error
          }
        }
        resolve();
      };

      xhr.onerror = () => {
        updateProgress(file.name, { status: "error", pct: 0, error: "Network error" });
        resolve();
      };

      xhr.send(formData);
    });
  }

  async function drainQueue() {
    while (queueRef.current.length > 0 && semaphoreRef.current < MAX_CONCURRENT) {
      const file = queueRef.current.shift();
      if (!file) break;
      semaphoreRef.current++;
      uploadFile(file).finally(() => {
        semaphoreRef.current--;
        void drainQueue();
      });
    }
  }

  function enqueue(files: FileList | File[]) {
    const arr = Array.from(files);
    queueRef.current.push(...arr);
    void drainQueue();
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    enqueue(e.dataTransfer.files);
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    if (e.target.files) enqueue(e.target.files);
    e.target.value = "";
  }

  const active = progress.filter((p) => p.status === "uploading");
  const done = progress.filter((p) => p.status === "done").length;
  const errors = progress.filter((p) => p.status === "error");

  return (
    <div className="space-y-2 px-3 py-2">
      <div
        onDragEnter={() => setIsDragging(true)}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={[
          "flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-6 text-center transition-colors",
          isDragging
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/30 hover:border-primary/60 hover:bg-accent/30",
        ].join(" ")}
      >
        <svg className="mb-2 h-6 w-6 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M12 16V8M9 11l3-3 3 3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1" strokeLinecap="round" />
        </svg>
        <p className="text-xs font-medium text-foreground">
          {isDragging ? "Drop to upload" : "Drop files here or click to browse"}
        </p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          PDF, DOCX, PPTX, XLSX, CSV, TXT, MD…
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          accept=".pdf,.docx,.pptx,.xlsx,.odt,.odp,.ods,.epub,.eml,.mbox,.html,.htm,.md,.csv,.txt"
          onChange={onInputChange}
        />
      </div>

      {/* Active uploads */}
      {active.map((p) => (
        <div key={p.filename} className="space-y-0.5">
          <div className="flex justify-between text-[10px] text-muted-foreground">
            <span className="truncate">{p.filename}</span>
            <span>{p.pct}%</span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${p.pct}%` }}
            />
          </div>
        </div>
      ))}

      {/* Errors */}
      {errors.map((p) => (
        <p key={p.filename} className="text-[10px] text-destructive">
          {p.filename}: {p.error}
        </p>
      ))}

      {/* Summary */}
      {done > 0 && active.length === 0 && errors.length === 0 && (
        <p className="text-center text-[10px] text-muted-foreground">
          {done} file{done !== 1 ? "s" : ""} uploaded
        </p>
      )}
    </div>
  );
}
