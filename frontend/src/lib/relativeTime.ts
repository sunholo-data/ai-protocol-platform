// Date grouping for list views. The compact "5m ago" label moved to the
// locale-aware formatter: `useFormat().relative(iso)` (src/i18n/format.ts).

export type DateGroup = "Today" | "Yesterday" | "Previous 7 days" | "Previous 30 days" | "Older";

const ORDER: DateGroup[] = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"];

/** Calendar bucket for a timestamp, by the viewer's local midnight. */
export function dateGroup(iso: string | null | undefined, now: number = Date.now()): DateGroup {
  const t = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(t)) return "Older";
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const day = 86_400_000;
  const start = midnight.getTime();
  if (t >= start) return "Today";
  if (t >= start - day) return "Yesterday";
  if (t >= start - 7 * day) return "Previous 7 days";
  if (t >= start - 30 * day) return "Previous 30 days";
  return "Older";
}

/** Split an already newest-first list into ordered, non-empty date groups. */
export function groupByDate<T>(items: T[], at: (item: T) => string | null | undefined, now: number = Date.now()) {
  const buckets = new Map<DateGroup, T[]>();
  for (const item of items) {
    const g = dateGroup(at(item), now);
    const list = buckets.get(g);
    if (list) list.push(item);
    else buckets.set(g, [item]);
  }
  return ORDER.filter((g) => buckets.has(g)).map((g) => ({ group: g, items: buckets.get(g)! }));
}
