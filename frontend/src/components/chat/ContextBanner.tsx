"use client";

export interface ActiveDocumentContext {
  folderName: string;
  docCount: number;
}

interface ContextBannerProps {
  context: ActiveDocumentContext | null;
}

export function ContextBanner({ context }: ContextBannerProps) {
  if (!context) return null;

  return (
    <div className="flex items-center gap-2 border-b bg-muted/50 px-4 py-2 text-xs text-muted-foreground">
      <svg
        className="h-3.5 w-3.5 shrink-0 text-teal-600"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <path
          d="M2 4a1 1 0 011-1h3.5l1.5 2H13a1 1 0 011 1v6a1 1 0 01-1 1H3a1 1 0 01-1-1V4z"
          strokeLinejoin="round"
        />
      </svg>
      <span>
        Analyzing <strong className="font-semibold text-foreground">{context.docCount}</strong>{" "}
        {context.docCount === 1 ? "document" : "documents"} from{" "}
        <strong className="font-semibold text-foreground">{context.folderName}</strong>
      </span>
    </div>
  );
}
