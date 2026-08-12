// Tool-call chip — compact spinner/check/error indicator with the tool name.
// MCP App UI surfaces are handled by MCPAppToolCallRouter (mounted by
// MessageBubble) using the tool DEFINITION's _meta.ui binding, per the
// MCP Apps spec (UI-by-reference). The chip always renders just a chip;
// it does NOT inspect resultContent for ui:// URIs anymore.
// See: docs/design/v6.1.0/mcp-app-integrations.md (M2A — frontend)

"use client";

import type { ToolCallState } from "@/hooks/useSkillAgent";

const MAX_TOOL_NAME = 32;

interface ToolCallChipProps {
  toolCall: ToolCallState;
}

export function ToolCallChip({ toolCall }: ToolCallChipProps) {
  const name =
    toolCall.name.length > MAX_TOOL_NAME
      ? toolCall.name.slice(0, MAX_TOOL_NAME) + "…"
      : toolCall.name;

  return (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-muted-foreground">
      {toolCall.status === "running" && <Spinner />}
      {toolCall.status === "success" && <CheckIcon />}
      {toolCall.status === "error" && <ErrorIcon />}
      <span className="font-mono">{name}</span>
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-3 w-3 animate-spin text-orange-500"
      viewBox="0 0 24 24"
      fill="none"
      aria-label="Running"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      className="h-3 w-3 text-green-600"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-label="Success"
    >
      <path d="M3 8l3.5 3.5L13 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg
      className="h-3 w-3 text-red-600"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-label="Error"
    >
      <path d="M4 4l8 8M12 4l-8 8" strokeLinecap="round" />
    </svg>
  );
}
