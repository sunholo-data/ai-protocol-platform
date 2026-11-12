"use client";

import * as Popover from "@radix-ui/react-popover";
import { Check, ListFilter, Search, X } from "lucide-react";
import { FLOATING_SURFACE } from "@/lib/surfaces";
import { cn } from "@/lib/utils";

interface SidebarSearchProps {
  value: string;
  onChange: (value: string) => void;
  /** Skills the user has chats with, for the filter. */
  skillOptions: { id: string; label: string }[];
  skillFilter: string | null;
  onSkillFilterChange: (skillId: string | null) => void;
}

/** The sidebar's one search, over chats and files, with the skill filter inside it. */
export function SidebarSearch({ value, onChange, skillOptions, skillFilter, onSkillFilterChange }: SidebarSearchProps) {
  const filtering = skillFilter !== null;
  return (
    <div className="flex min-h-[44px] items-center gap-1.5 rounded-[var(--radius)] border border-border bg-background px-2.5 focus-within:border-ring md:min-h-[36px]">
      <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) {
            e.preventDefault();
            onChange("");
          }
        }}
        placeholder="Search chats and files"
        aria-label="Search chats and files"
        autoComplete="off"
        spellCheck={false}
        className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button type="button" onClick={() => onChange("")} aria-label="Clear search" className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X className="h-4 w-4" aria-hidden />
        </button>
      ) : null}
      {skillOptions.length > 1 || filtering ? (
        <Popover.Root>
          <Popover.Trigger asChild>
            <button
              type="button"
              aria-label={filtering ? "Filter by skill (on)" : "Filter by skill"}
              className={cn(
                "relative rounded p-0.5 hover:bg-muted hover:text-foreground",
                filtering ? "text-primary" : "text-muted-foreground",
              )}
            >
              <ListFilter className="h-4 w-4" aria-hidden />
              {filtering ? <span aria-hidden className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary" /> : null}
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content align="end" sideOffset={6} className={`${FLOATING_SURFACE} w-56 p-1`}>
              <p className="px-2 py-1 text-xs text-muted-foreground">Show chats with</p>
              {[{ id: null as string | null, label: "All skills" }, ...skillOptions].map((o) => (
                <Popover.Close asChild key={o.id ?? "all"}>
                  <button
                    type="button"
                    onClick={() => onSkillFilterChange(o.id)}
                    className="flex min-h-[40px] w-full items-center justify-between gap-2 rounded-[var(--radius)] px-2 text-left text-sm hover:bg-muted md:min-h-[32px]"
                  >
                    <span className="truncate">{o.label}</span>
                    {skillFilter === o.id ? <Check className="h-4 w-4 shrink-0" aria-hidden /> : null}
                  </button>
                </Popover.Close>
              ))}
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      ) : null}
    </div>
  );
}
