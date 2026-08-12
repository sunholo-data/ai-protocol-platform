"use client";

import { usePDFInfo } from "@/hooks/usePDFInfo";

interface PDFCardProps {
  url: string;
}

function PDFIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

export function PDFCard({ url }: PDFCardProps) {
  const { info, loading } = usePDFInfo(url);

  const filename = info?.filename ?? decodeURIComponent(url.split("/").pop() ?? "document.pdf");
  const pages = info?.pages;

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 rounded border border-border bg-muted px-3 py-2 text-sm text-foreground no-underline hover:bg-muted/80"
      aria-label={`Open PDF: ${filename}`}
    >
      <PDFIcon />
      <span className="max-w-[200px] truncate font-medium">{filename}</span>
      {loading && (
        <span className="text-xs text-muted-foreground">…</span>
      )}
      {!loading && pages != null && (
        <span className="rounded bg-background px-1.5 py-0.5 text-xs text-muted-foreground">
          {pages}p
        </span>
      )}
      <DownloadIcon />
    </a>
  );
}
