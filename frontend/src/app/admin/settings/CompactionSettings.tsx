// Conversation-compaction levers (v6.23.0, tuning console 1b + second pass 1e).
//
// Every field is "empty = use the shipped default", so clearing a box restores
// coded behaviour rather than writing a zero. That matters because these values
// change ANSWER QUALITY silently — a degraded answer looks identical to a good
// one — which is why this section leads with a warning and why the retention
// floor is explained inline (it gates whether compaction fires at all, and that
// surprise produced a wrong test result before it was understood).
//
// Saves the whole `compaction` block in one PUT so a cleared field is sent as
// null; the backend stores the block whole for the same reason.

"use client";

import { useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";

export type CompactionSettings = {
  enabled?: boolean | null;
  tokenThreshold?: number | null;
  eventRetentionSize?: number | null;
  summarizerModel?: string | null;
  summarizerPrompt?: string | null;
  secondPassEnabled?: boolean | null;
  secondPassIdleSeconds?: number | null;
};

const ENDPOINT = "/api/proxy/api/admin/platform-config";
const PLACEHOLDER = "{conversation_history}";

/** "" → null (restore the shipped default), otherwise a positive integer. */
function toNumberOrNull(raw: string): number | null | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0 || !Number.isInteger(n)) return undefined; // invalid
  return n;
}

export function CompactionSettingsSection({
  initial,
  defaultPrompt = "",
  onSaved,
}: {
  initial: CompactionSettings;
  /** The SHIPPED summariser prompt, so the editor opens on the real text
   * instead of an empty box. Unchanged text saves as null (see `save`). */
  defaultPrompt?: string;
  onSaved?: (updatedBy?: string, updatedAt?: number) => void;
}) {
  const [enabled, setEnabled] = useState(initial.enabled ?? true);
  const [tokenThreshold, setTokenThreshold] = useState(initial.tokenThreshold?.toString() ?? "");
  const [retention, setRetention] = useState(initial.eventRetentionSize?.toString() ?? "");
  const [model, setModel] = useState(initial.summarizerModel ?? "");
  const [prompt, setPrompt] = useState(initial.summarizerPrompt ?? defaultPrompt);
  const [secondPass, setSecondPass] = useState(initial.secondPassEnabled ?? false);
  const [idleMinutes, setIdleMinutes] = useState(
    initial.secondPassIdleSeconds ? Math.round(initial.secondPassIdleSeconds / 60).toString() : "",
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Client-side mirrors of the backend's write-time validation. The server is
  // still the gate (a stale tab, a curl, a script) — this only shortens the loop.
  const promptInvalid = prompt.trim().length > 0 && !prompt.includes(PLACEHOLDER);
  // Unedited text is NOT an override. Saving the default verbatim would freeze
  // a copy that no longer tracks improvements we ship to the shipped prompt —
  // the whole point of "empty = use the default", preserved now that the box is
  // pre-filled.
  const promptIsDefault = prompt.trim() === defaultPrompt.trim();
  const thresholdInvalid = toNumberOrNull(tokenThreshold) === undefined;
  const retentionInvalid = toNumberOrNull(retention) === undefined;
  const idleInvalid = toNumberOrNull(idleMinutes) === undefined;
  const blocked = promptInvalid || thresholdInvalid || retentionInvalid || idleInvalid;

  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const idle = toNumberOrNull(idleMinutes);
      const compaction: CompactionSettings = {
        enabled,
        tokenThreshold: toNumberOrNull(tokenThreshold) ?? null,
        eventRetentionSize: toNumberOrNull(retention) ?? null,
        summarizerModel: model.trim() || null,
        summarizerPrompt: promptIsDefault ? null : prompt.trim() || null,
        secondPassEnabled: secondPass,
        secondPassIdleSeconds: idle ? idle * 60 : null,
      };
      const r = await fetchWithAuth(ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ compaction }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        setNotice(detail?.detail || "Could not save compaction settings.");
        return;
      }
      const cfg = await r.json();
      onSaved?.(cfg.updatedBy, cfg.updatedAt);
      setNotice("Saved. Applies to new turns within about a minute — no deploy needed.");
    } catch {
      setNotice("Could not save compaction settings.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="text-base font-semibold">Conversation compaction</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        When a conversation grows past the token threshold, older turns are replaced by an AI summary before the model
        sees them. <strong>These settings change answer quality silently</strong> — the transcript on screen still shows
        every turn, so a degraded answer looks identical to a good one. Watch the &quot;History summarised&quot; marker in
        the Activity tab after changing anything.
      </p>
      <p className="mt-2 text-xs text-muted-foreground">
        Leave a field empty to use the shipped default for that lever.
      </p>

      <label className="mt-4 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>Compaction enabled (token-pressure trigger)</span>
      </label>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field
          label="Token threshold"
          hint="Prompt size that triggers a compaction. Default is per model family (1M-context: 250,000)."
          error={thresholdInvalid ? "Must be a whole number." : null}
        >
          <input
            type="text"
            inputMode="numeric"
            aria-label="Token threshold"
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder="250000"
            value={tokenThreshold}
            onChange={(e) => setTokenThreshold(e.target.value)}
            disabled={!enabled}
          />
        </Field>

        <Field
          label="Raw events retained"
          // The non-obvious second effect, stated where it can't be missed.
          hint="Recent events kept verbatim. Also a FLOOR: compaction cannot fire until a conversation exceeds this many events, however large it is. Default 60."
          error={retentionInvalid ? "Must be a whole number." : null}
        >
          <input
            type="text"
            inputMode="numeric"
            aria-label="Raw events retained"
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder="60"
            value={retention}
            onChange={(e) => setRetention(e.target.value)}
            disabled={!enabled}
          />
        </Field>

        <Field label="Summariser model" hint="Tier used to write the summary. Default: pro.">
          <select
            aria-label="Summariser model"
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={!enabled}
          >
            <option value="">Default (pro)</option>
            <option value="pro">pro — slower, keeps more detail</option>
            <option value="smart">smart</option>
            <option value="lite">lite — fast and cheap</option>
          </select>
        </Field>
      </div>

      <div className="mt-4">
        <div className="flex items-baseline justify-between">
          <label className="block text-sm font-medium">Summariser prompt</label>
          {defaultPrompt && !promptIsDefault && (
            <button
              type="button"
              onClick={() => setPrompt(defaultPrompt)}
              className="text-xs underline hover:no-underline"
            >
              Reset to shipped prompt
            </button>
          )}
        </div>
        <textarea
          aria-label="Summariser prompt"
          className="h-56 w-full rounded-md border px-3 py-2 font-mono text-xs"
          placeholder={`Must contain ${PLACEHOLDER}.`}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          disabled={!enabled}
        />
        {promptInvalid ? (
          <p className="mt-1 text-xs text-red-600">
            Must contain {PLACEHOLDER} — it is where the conversation is substituted in.
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">
            What the summariser is told to preserve — the most consequential lever here.{" "}
            {promptIsDefault
              ? "This is the shipped prompt; leaving it unchanged keeps you on future improvements to it."
              : "Edited — this overrides the shipped prompt until you reset it."}
          </p>
        )}
      </div>

      <div className="mt-6 rounded-md border border-dashed p-3">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={secondPass} onChange={(e) => setSecondPass(e.target.checked)} />
          <span>Second pass — re-summarise idle conversations</span>
        </label>
        <p className="mt-1 text-xs text-muted-foreground">
          Once a conversation goes quiet, re-derive its summary from the original turns with more time and a stronger
          model, replacing the one written mid-chat. Costs nothing in chat latency. Requires a task queue in this
          environment — if none is configured the setting is ignored and the backend logs a warning.
        </p>
        <div className="mt-3 max-w-xs">
          <Field
            label="Idle wait (minutes)"
            hint="How long a conversation must be quiet first. Default 45."
            error={idleInvalid ? "Must be a whole number of minutes." : null}
          >
            <input
              type="text"
              inputMode="numeric"
              aria-label="Idle wait (minutes)"
              className="w-full rounded-md border px-3 py-2 text-sm"
              placeholder="45"
              value={idleMinutes}
              onChange={(e) => setIdleMinutes(e.target.value)}
              disabled={!secondPass}
            />
          </Field>
        </div>
      </div>

      <div className="mt-4 flex items-center justify-end gap-3">
        {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
        <button
          onClick={save}
          disabled={busy || blocked}
          className="rounded-md border px-3 py-2 text-sm hover:bg-muted/40 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save compaction settings"}
        </button>
      </div>
    </section>
  );
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint: string;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium">{label}</label>
      {children}
      {error ? (
        <p className="mt-1 text-xs text-red-600">{error}</p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}
