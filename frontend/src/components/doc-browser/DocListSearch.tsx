"use client";

interface DocListSearchProps {
  value: string;
  onChange: (q: string) => void;
}

export function DocListSearch({ value, onChange }: DocListSearchProps) {
  return (
    <div className="relative px-3 py-2">
      <svg
        className="pointer-events-none absolute left-5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden="true"
      >
        <circle cx="6.5" cy="6.5" r="4" />
        <path d="M10 10l3 3" strokeLinecap="round" />
      </svg>
      <input
        type="search"
        placeholder="Search documents…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border bg-background py-1.5 pl-7 pr-3 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
      />
    </div>
  );
}
