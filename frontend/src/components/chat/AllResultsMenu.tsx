"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Activity, ChevronDown, FileText } from "lucide-react";
import { FLOATING_SURFACE } from "@/lib/surfaces";
import { useFormat } from "@/i18n";

export interface ResultEntry {
  surfaceId: string;
  title?: string | null;
  kind?: string | null;
  createdAt: number;
}

const ITEM =
  "flex min-h-[40px] w-full cursor-pointer select-none items-center gap-3 rounded-[var(--radius)] px-3 text-left text-sm text-foreground outline-none data-[highlighted]:bg-muted";

/**
 * Every result from this chat, one click away — the workbench's tabs are the
 * open ones; this lists them all (closed ones too) plus the activity log.
 */
export function AllResultsMenu({
  results,
  documentName,
  onOpen,
}: {
  results: ResultEntry[];
  /** The open document, when there is one. */
  documentName?: string | null;
  onOpen: (tabId: string) => void;
}) {
  const now = Date.now();
  const fmt = useFormat();
  const count = results.length + (documentName ? 1 : 0);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="flex shrink-0 items-center gap-1 self-center rounded-[var(--radius)] px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground"
        >
          All results{count > 0 ? ` · ${count}` : ""}
          <ChevronDown className="h-4 w-4" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={4} className={`${FLOATING_SURFACE} w-72 p-1`}>
          {count > 0 ? (
            <DropdownMenu.Label className="px-3 pb-1 pt-1.5 text-xs text-muted-foreground">In this chat</DropdownMenu.Label>
          ) : (
            <p className="px-3 py-2 text-sm text-muted-foreground">No results yet.</p>
          )}
          {results.map((r) => (
            <DropdownMenu.Item key={r.surfaceId} className={ITEM} onSelect={() => onOpen(r.surfaceId)}>
              <span className="min-w-0 flex-1 truncate">{r.title || r.kind || "Result"}</span>
              {r.createdAt ? (
                <span className="shrink-0 text-xs text-muted-foreground">{fmt.relative(r.createdAt, now)}</span>
              ) : null}
            </DropdownMenu.Item>
          ))}
          {documentName ? (
            <DropdownMenu.Item className={ITEM} onSelect={() => onOpen("document")}>
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{documentName}</span>
            </DropdownMenu.Item>
          ) : null}
          <DropdownMenu.Separator className="my-1 h-px bg-border" />
          <DropdownMenu.Item className={`${ITEM} text-muted-foreground`} onSelect={() => onOpen("activity")}>
            <Activity className="h-4 w-4 shrink-0" aria-hidden />
            Activity log
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
