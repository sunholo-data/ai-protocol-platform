// Skill access-control editor for Skill Studio. Closes the 9.2 gap: the Studio
// loaded accessControl into the draft but never let you edit or save it (skills
// were stuck private). Supports the backend's 5 access types
// (backend/db/models/access.py): public / private / tagged / domain / specific.
//
// The draft carries accessControl as an opaque Record; this component reads/writes
// the { type, domain?, emails?, tags? } shape.

"use client";

type Access = {
  type?: string;
  domain?: string | null;
  emails?: string[] | null;
  tags?: string[] | null;
};

const TYPES: { v: string; label: string }[] = [
  { v: "private", label: "Private — only you (the owner)" },
  { v: "public", label: "Public — anyone (listed in the marketplace)" },
  { v: "tagged", label: "Tagged — users who hold a group tag" },
  { v: "domain", label: "Domain — users of an email domain" },
  { v: "specific", label: "Specific — a list of emails" },
];

const csv = (a?: string[] | null): string => (a ?? []).join(", ");
const toList = (s: string): string[] | null => {
  const x = s
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  return x.length ? x : null;
};

export function AccessControlEditor({
  value,
  onChange,
}: {
  value?: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const v = (value ?? { type: "private" }) as Access;
  const type = v.type ?? "private";

  return (
    <div className="space-y-2">
      <label className="block">
        <span className="mb-1 block text-sm font-medium">Who can use this skill</span>
        <select
          className="w-full rounded-md border px-3 py-2 text-sm"
          value={type}
          onChange={(e) => {
            const nextType = e.target.value;
            // Security gate (CLAUDE.md): making a skill public lists it in the
            // unauthenticated marketplace AND exposes its A2A agent card to
            // anyone. Require an explicit confirm — never a silent switch.
            if (nextType === "public" && type !== "public") {
              const ok = window.confirm(
                "Make this skill PUBLIC?\n\nIt will be listed in the marketplace and reachable by anyone — unauthenticated — including its A2A agent card. Do NOT make a skill that touches confidential content public.",
              );
              if (!ok) return; // keep the current type
            }
            onChange({ ...v, type: nextType });
          }}
        >
          {TYPES.map((t) => (
            <option key={t.v} value={t.v}>
              {t.label}
            </option>
          ))}
        </select>
      </label>

      {type === "tagged" && (
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">
            Group tags (comma-separated) — a user needs any one of these
          </span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={csv(v.tags)}
            placeholder="ONE, aitana-admin"
            onChange={(e) => onChange({ ...v, tags: toList(e.target.value) })}
          />
        </label>
      )}

      {type === "domain" && (
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">Email domain</span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={v.domain ?? ""}
            placeholder="acme-energy.example"
            onChange={(e) => onChange({ ...v, domain: e.target.value || null })}
          />
        </label>
      )}

      {type === "specific" && (
        <label className="block">
          <span className="mb-1 block text-xs text-muted-foreground">
            Allowed emails (comma-separated)
          </span>
          <input
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={csv(v.emails)}
            placeholder="alice@example.com, bob@example.com"
            onChange={(e) => onChange({ ...v, emails: toList(e.target.value) })}
          />
        </label>
      )}
    </div>
  );
}
