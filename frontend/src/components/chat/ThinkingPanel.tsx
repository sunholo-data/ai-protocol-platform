"use client";

import { useEffect, useState } from "react";
import { Spinner } from "@/components/ui/spinner";

interface ThinkingPanelProps {
  content: string;
  isThinking: boolean;
}

export function ThinkingPanel({ content, isThinking }: ThinkingPanelProps) {
  const [expanded, setExpanded] = useState(true);

  // Auto-collapse when thinking finishes
  useEffect(() => {
    if (!isThinking) setExpanded(false);
  }, [isThinking]);

  return (
    <div className="mb-2 rounded border border-orange-200 bg-orange-50/50 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1.5 text-left text-orange-700"
      >
        {isThinking && (
          <Spinner size="xs" label="Thinking" />
        )}
        <span className="font-medium">{isThinking ? "Thinking…" : "Thought process"}</span>
        <svg
          className={`ml-auto h-3 w-3 shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`}
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden="true"
        >
          <path d="M4 6l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {expanded && (
        <p className="whitespace-pre-wrap px-2 pb-2 text-orange-800/70">{content}</p>
      )}
    </div>
  );
}
