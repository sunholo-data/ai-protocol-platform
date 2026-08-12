// Analytics — admin session browser + trace viewer (v6.9.0, redesigned v6.23.x).
//
// Lists past chat sessions (the chat_sessions mirror) and opens a full trace
// reconstructed by the backend from ADK events. The trace renders as ONE
// chronological timeline — messages, tool calls and delegations interleaved in
// the order they actually happened — using the same rendering primitives as the
// chat Activity tab (@/components/activity/*), plus a Raw JSON view for the
// unvarnished stream. Owner + skill facet selectors (with counts) make it easy
// to jump between users and their sessions.
//
// Aitana-admin gated by the backend; a 403 renders the "admins only" state.
// Never-silent: loading / empty / error paths all render.

"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import { SignInRequired } from "@/components/chat/SignInRequired";
import { ArrowIcon, CopyButton, StatusDot, WrenchIcon } from "@/components/activity/bits";
import { ToolCallDetails, hasToolDetail } from "@/components/activity/ToolCallDetails";
import { DocIcon } from "@/components/icons";
import { buildTimeline, type TimelineItem, type TraceDeleg, type TraceMsg, type TraceTool } from "./timeline";

type SessionRow = {
  session_id: string;
  skill_id: string;
  skill_label?: string;
  owner_uid: string;
  owner_email?: string;
  owner_name?: string;
  title: string;
  turn_count: number;
  document_count?: number;
  first_message_at: string;
  last_message_at: string;
  archived: boolean;
  transcript_lost?: boolean;
};

type OwnerFacet = { uid: string; email: string; name: string; sessions: number; last_active: string };
type SkillFacet = { id: string; label: string; sessions: number };

/** Best friendly label for an owner: name, else email, else the raw uid. */
function ownerLabel(o: { owner_name?: string; owner_email?: string; owner_uid: string }): string {
  return (o.owner_name || "").trim() || (o.owner_email || "").trim() || o.owner_uid || "—";
}

type Trace = {
  session_id: string;
  skill_id: string;
  skill_label?: string;
  owner_uid: string;
  owner_email?: string;
  owner_name?: string;
  title?: string;
  turn_count?: number;
  first_message_at?: string;
  last_message_at?: string;
  documents?: { id: string; name: string }[];
  session_start_ts?: number | null;
  event_count?: number;
  transcript_available?: boolean;
  messages: TraceMsg[];
  tools: TraceTool[];
  delegations: TraceDeleg[];
};

const API = "/api/proxy/api/admin/analytics";

type ListState = "loading" | "ok" | "forbidden" | "error";
type TraceState = "idle" | "loading" | "ok" | "error";

function fmtTime(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** Relative "3h ago" for list rows; exact time on hover. */
function fmtAgo(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return iso;
  const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(d).toLocaleDateString();
}

function clockTime(ts: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString();
}

export default function AnalyticsPage() {
  const { user, loading } = useAuth();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [owners, setOwners] = useState<OwnerFacet[]>([]);
  const [skills, setSkills] = useState<SkillFacet[]>([]);
  const [hiddenEmpty, setHiddenEmpty] = useState(0);
  const [state, setState] = useState<ListState>("loading");
  const [q, setQ] = useState("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [skillFilter, setSkillFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [traceState, setTraceState] = useState<TraceState>("idle");

  const loadSessions = useCallback((query: string, owner: string, skill: string) => {
    setState("loading");
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (owner) params.set("owner_uid", owner);
    if (skill) params.set("skill_id", skill);
    const qs = params.toString();
    fetchWithAuth(qs ? `${API}/sessions?${qs}` : `${API}/sessions`)
      .then(async (r) => {
        if (r.status === 403) return setState("forbidden");
        if (!r.ok) return setState("error");
        const data = await r.json();
        setRows(data.sessions ?? []);
        setOwners(data.owners ?? []);
        setSkills(data.skills ?? []);
        setHiddenEmpty(data.hidden_empty ?? 0);
        setState("ok");
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => {
    if (loading || !user) return;
    loadSessions(q.trim(), ownerFilter, skillFilter);
    // q is submit-driven (the form below), not keystroke-driven.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, user, loadSessions, ownerFilter, skillFilter]);

  const openTrace = useCallback((sid: string) => {
    setSelected(sid);
    setTrace(null);
    setTraceState("loading");
    fetchWithAuth(`${API}/sessions/${encodeURIComponent(sid)}`)
      .then(async (r) => {
        if (!r.ok) return setTraceState("error");
        setTrace(await r.json());
        setTraceState("ok");
      })
      .catch(() => setTraceState("error"));
  }, []);

  if (loading) return <Centered>Loading…</Centered>;
  if (!user) return <SignInRequired />;
  if (state === "forbidden") {
    return (
      <Centered>
        <div className="max-w-md text-center">
          <h1 className="mb-2 text-lg font-semibold">Admins only</h1>
          <p className="text-sm text-muted-foreground">
            Analytics requires the <code>aitana-admin</code> group.
          </p>
        </div>
      </Centered>
    );
  }

  const hasFilters = Boolean(ownerFilter || skillFilter || q.trim());

  return (
    <main className="mx-auto max-w-[1500px] p-6">
      <header className="mb-4">
        <h1 className="text-xl font-semibold">Analytics</h1>
        <p className="text-sm text-muted-foreground">
          Browse and search past sessions; open a full trace of messages, tool calls, and delegations
          in the order they happened.
        </p>
      </header>

      <form
        className="mb-4 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          loadSessions(q.trim(), ownerFilter, skillFilter);
        }}
      >
        <input
          className="min-w-[16rem] flex-1 rounded-md border px-3 py-2 text-sm"
          placeholder="Search title, owner, or skill…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          aria-label="Filter by user"
          className="rounded-md border bg-background px-2 py-2 text-sm"
          value={ownerFilter}
          onChange={(e) => setOwnerFilter(e.target.value)}
        >
          <option value="">All users ({owners.length})</option>
          {owners.map((o) => (
            <option key={o.uid} value={o.uid}>
              {ownerLabel({ owner_name: o.name, owner_email: o.email, owner_uid: o.uid })} ({o.sessions})
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by skill"
          className="rounded-md border bg-background px-2 py-2 text-sm"
          value={skillFilter}
          onChange={(e) => setSkillFilter(e.target.value)}
        >
          <option value="">All skills ({skills.length})</option>
          {skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label || s.id} ({s.sessions})
            </option>
          ))}
        </select>
        <button type="submit" className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40">
          Search
        </button>
        {hasFilters && (
          <button
            type="button"
            className="rounded-md px-2 py-2 text-sm text-muted-foreground hover:text-foreground"
            onClick={() => {
              setQ("");
              setOwnerFilter("");
              setSkillFilter("");
            }}
          >
            Clear
          </button>
        )}
      </form>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.7fr)]">
        {/* Session list */}
        <section className="rounded-lg border">
          <div className="border-b px-3 py-2 text-xs font-medium uppercase text-muted-foreground">
            Sessions {state === "ok" && `(${rows.length})`}
          </div>
          {state === "loading" && <Note>Loading sessions…</Note>}
          {state === "error" && <Note>Could not load sessions. Try again.</Note>}
          {state === "ok" && rows.length === 0 && <Note>No sessions match.</Note>}
          {state === "ok" && rows.length > 0 && (
            <ul className="max-h-[75vh] divide-y overflow-auto">
              {rows.map((r) => (
                <li key={r.session_id}>
                  <button
                    onClick={() => openTrace(r.session_id)}
                    className={
                      "block w-full px-3 py-2 text-left text-sm hover:bg-muted/40 " +
                      (selected === r.session_id ? "bg-muted/60" : "")
                    }
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{r.title || "(untitled)"}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {r.transcript_lost && (
                          <span className="rounded-full border border-amber-500/50 px-1.5 text-[10px] uppercase text-amber-600 dark:text-amber-400">
                            transcript lost
                          </span>
                        )}
                        {r.archived && (
                          <span className="rounded-full border px-1.5 text-[10px] uppercase text-muted-foreground">
                            archived
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {ownerLabel(r)}
                      {r.owner_email && r.owner_name ? ` · ${r.owner_email}` : ""}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                      {(r.skill_label || r.skill_id) && (
                        <span className="rounded-full bg-muted px-1.5 py-px text-[10px] text-foreground/70">
                          {r.skill_label || r.skill_id}
                        </span>
                      )}
                      <span>
                        {r.turn_count} {r.turn_count === 1 ? "turn" : "turns"}
                      </span>
                      {(r.document_count ?? 0) > 0 && (
                        <span>
                          · {r.document_count} {r.document_count === 1 ? "doc" : "docs"}
                        </span>
                      )}
                      <span title={fmtTime(r.last_message_at)}>· {fmtAgo(r.last_message_at)}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {/* The list shrank on purpose — say so rather than hiding rows
              silently (CLAUDE.md #8). These are bootstrap husks nothing was
              ever sent to; they stay reachable via the API by id. */}
          {state === "ok" && hiddenEmpty > 0 && (
            <div className="border-t px-3 py-1.5 text-[11px] text-muted-foreground/70">
              {hiddenEmpty} never-used session{hiddenEmpty === 1 ? "" : "s"} hidden
            </div>
          )}
        </section>

        {/* Trace detail */}
        <section className="rounded-lg border">
          <div className="border-b px-3 py-2 text-xs font-medium uppercase text-muted-foreground">
            Trace
          </div>
          {traceState === "idle" && <Note>Select a session to view its trace.</Note>}
          {traceState === "loading" && <Note>Loading trace…</Note>}
          {traceState === "error" && <Note>Could not load this trace.</Note>}
          {traceState === "ok" && trace && <TraceView trace={trace} />}
        </section>
      </div>
    </main>
  );
}

/** Header metadata block: who / which skill / when / how much. */
function TraceHeader({ trace }: { trace: Trace }) {
  const docs = trace.documents ?? [];
  return (
    <div className="rounded-md border bg-muted/10 px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{trace.title || "(untitled)"}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {ownerLabel(trace)}
            {trace.owner_email ? ` · ${trace.owner_email}` : ""}
            {trace.skill_label || trace.skill_id ? ` · ${trace.skill_label || trace.skill_id}` : ""}
          </div>
        </div>
        <CopyButton text={trace.session_id} label="Copy id" />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
        <div>
          <dt className="text-[10px] uppercase text-muted-foreground/60">Started</dt>
          <dd>{fmtTime(trace.first_message_at || "")}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-muted-foreground/60">Last activity</dt>
          <dd>{fmtTime(trace.last_message_at || "")}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-muted-foreground/60">Turns</dt>
          <dd>{trace.turn_count ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-muted-foreground/60">Events</dt>
          <dd>{trace.event_count ?? "—"}</dd>
        </div>
      </dl>
      {docs.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {docs.map((d) => (
            <span
              key={d.id}
              title={d.id}
              className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] text-foreground/70"
            >
              <DocIcon className="h-3 w-3 text-muted-foreground" />
              {d.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Expandable tool row in the timeline — same detail body as the chat Activity tab. */
function TimelineToolRow({ item }: { item: Extract<TimelineItem, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  const hasDetail = hasToolDetail(item.argsJson, item.resultContent);
  return (
    <li className="rounded-md text-xs text-muted-foreground">
      <button
        type="button"
        disabled={!hasDetail}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent"
        aria-expanded={hasDetail ? open : undefined}
      >
        <WrenchIcon />
        <span className="flex-1 truncate font-mono text-[11px] text-foreground/80">{item.name}</span>
        <StatusDot status={item.status} />
        {item.ts ? (
          <time
            dateTime={new Date(item.ts).toISOString()}
            title={new Date(item.ts).toLocaleString()}
            className="shrink-0 tabular-nums text-[10px] text-muted-foreground/70"
          >
            {clockTime(item.ts)}
          </time>
        ) : null}
        {hasDetail && (
          <span aria-hidden className={`shrink-0 text-muted-foreground/60 transition-transform ${open ? "rotate-90" : ""}`}>
            ›
          </span>
        )}
      </button>
      {open && hasDetail && (
        <div className="ml-6 mb-1.5 mr-2">
          <ToolCallDetails argsJson={item.argsJson} resultContent={item.resultContent} />
        </div>
      )}
    </li>
  );
}

function TimelineMessageRow({ item }: { item: Extract<TimelineItem, { kind: "message" }> }) {
  const who = item.role === "user" ? "User" : item.agentLabel || "Assistant";
  return (
    <li
      className={
        "rounded-lg border px-3 py-2 text-sm " + (item.role === "user" ? "bg-muted/30" : "bg-background")
      }
    >
      <div className="mb-0.5 flex items-center justify-between gap-2">
        <span className="text-[10px] font-medium uppercase text-muted-foreground">{who}</span>
        {item.ts ? (
          <time
            dateTime={new Date(item.ts).toISOString()}
            title={new Date(item.ts).toLocaleString()}
            className="tabular-nums text-[10px] text-muted-foreground/70"
          >
            {clockTime(item.ts)}
          </time>
        ) : null}
      </div>
      <div className="whitespace-pre-wrap break-words">{item.content}</div>
    </li>
  );
}

function TraceView({ trace }: { trace: Trace }) {
  const [view, setView] = useState<"timeline" | "raw">("timeline");
  const timeline = buildTimeline(trace);

  return (
    <div className="max-h-[75vh] space-y-3 overflow-auto p-3">
      <TraceHeader trace={trace} />

      {trace.transcript_available === false && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          Transcript unavailable — this session&rsquo;s stored messages could not be read (the
          canonical session is missing while its index row survives). The session metadata is still
          shown; the message history can&rsquo;t be reconstructed.
        </div>
      )}

      <div className="flex items-center gap-1" role="tablist" aria-label="Trace view">
        {(["timeline", "raw"] as const).map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={view === v}
            onClick={() => setView(v)}
            className={
              "rounded-md px-2 py-1 text-xs " +
              (view === v ? "bg-muted font-medium text-foreground" : "text-muted-foreground hover:text-foreground")
            }
          >
            {v === "timeline" ? `Timeline (${timeline.length})` : "Raw JSON"}
          </button>
        ))}
      </div>

      {view === "raw" ? (
        <div className="rounded-md border">
          <div className="flex items-center justify-between border-b px-2 py-1">
            <span className="text-[10px] font-medium uppercase text-muted-foreground/60">
              Full trace payload
            </span>
            <CopyButton text={JSON.stringify(trace, null, 2)} />
          </div>
          <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words p-2 font-mono text-[11px] leading-relaxed text-foreground/80">
            {JSON.stringify(trace, null, 2)}
          </pre>
        </div>
      ) : timeline.length === 0 ? (
        <Note>
          {trace.transcript_available === false ? "Transcript unavailable (see above)." : "No activity in this session."}
        </Note>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {timeline.map((item) => {
            if (item.kind === "message") return <TimelineMessageRow key={item.key} item={item} />;
            if (item.kind === "tool") return <TimelineToolRow key={item.key} item={item} />;
            return (
              <li key={item.key} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground">
                <ArrowIcon />
                <span className="min-w-0 flex-1 truncate">
                  {item.mode === "suggest" ? "Suggested " : "Delegated to "}
                  <span className="font-medium text-foreground/80">{item.targetDisplay}</span>
                </span>
                {item.ts ? (
                  <time
                    dateTime={new Date(item.ts).toISOString()}
                    title={new Date(item.ts).toLocaleString()}
                    className="shrink-0 tabular-nums text-[10px] text-muted-foreground/70"
                  >
                    {clockTime(item.ts)}
                  </time>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Note({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-6 text-center text-sm text-muted-foreground">{children}</div>;
}

function Centered({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center p-8">{children}</main>;
}
