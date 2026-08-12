// Skill Studio — the authoring page.
//
// `[skillId]` is either the literal `new` (create a fresh skill) or an existing
// skill id (load via GET, then edit). Left: a builder form bound to draft state.
// Right: the <AuthoringCopilot> panel (a chat to the `skill-authoring-assistant`
// skill) whose proposals mutate the SAME draft. Nothing persists until Save,
// which does PUT (existing) or POST (new) via fetchWithAuth.
//
// The whole route is gated behind NEXT_PUBLIC_ENABLE_SKILL_STUDIO === "true".

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { use } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/AuthContext";
import { fetchWithAuth } from "@/lib/apiClient";
import {
  combineInstructions,
  parseInstructions,
  type StructuredInstructions,
} from "@/components/studio/structuredInstructions";
import { AGUIProvider } from "@/providers/AGUIProvider";
import { DelegationEditor } from "@/components/studio/DelegationEditor";
import { AccessControlEditor } from "@/components/studio/AccessControlEditor";
import { SignInRequired } from "@/components/chat/SignInRequired";
import {
  AuthoringCopilot,
  threadStorageKey,
} from "@/components/studio/AuthoringCopilot";
import {
  applyProposal,
  type Proposal,
  type StudioDraft,
} from "@/components/studio/applyProposal";
import type { InteractionStyle, Skill, SkillVoiceConfig } from "@/types/skill";
import { DEFAULT_AVATARS, randomGlyphAvatar } from "@/lib/defaultAvatars";

const STUDIO_ENABLED = process.env.NEXT_PUBLIC_ENABLE_SKILL_STUDIO === "true";

/** The copilot always talks to this platform skill (seeded backend M3). */
const AUTHORING_SKILL_ID = "skill-authoring-assistant";

/** Sentinel owner uid for platform-owned (read-only) skills. Mirrors
 * backend skills/platform.py PLATFORM_OWNER_UID. */
const PLATFORM_OWNER_UID = "aitana-platform";

const INTERACTION_STYLES: InteractionStyle[] = [
  "concise",
  "rigorous",
  "warm",
  "socratic",
];

type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "ok"; message: string }
  | { status: "error"; message: string };

export default function StudioPage({
  params,
}: {
  params: Promise<{ skillId: string }>;
}) {
  const { skillId } = use(params);

  if (!STUDIO_ENABLED) {
    return (
      <main className="flex min-h-screen items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">Skill Studio is disabled.</p>
      </main>
    );
  }

  return <StudioGate skillId={skillId} />;
}

function StudioGate({ skillId }: { skillId: string }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }
  if (!user) return <SignInRequired />;
  return <StudioInner skillId={skillId} />;
}

function StudioInner({ skillId }: { skillId: string }) {
  const isNew = skillId === "new";
  const router = useRouter();
  const [draft, setDraft] = useState<StudioDraft>(() => emptyDraft());
  const [loadingSkill, setLoadingSkill] = useState(!isNew);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>({ status: "idle" });
  // Snapshot of the last loaded/saved draft, used to detect unsaved edits so
  // Cancel can warn before discarding. Compared as JSON — a false "dirty" only
  // triggers an extra confirm, never data loss.
  const [pristine, setPristine] = useState<string>(() =>
    JSON.stringify(emptyDraft()),
  );

  // Seed the copilot thread from localStorage so a reload resumes the same
  // conversation for this edited skill. Read once at mount.
  const seededThreadId = useMemo(() => {
    if (typeof window === "undefined") return undefined;
    return window.localStorage.getItem(threadStorageKey(skillId)) ?? undefined;
  }, [skillId]);

  // Load an existing skill into the draft.
  useEffect(() => {
    if (isNew) return;
    let cancelled = false;
    setLoadingSkill(true);
    fetchWithAuth(`/api/proxy/api/skills/${skillId}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as Skill;
        if (cancelled) return;
        const loaded = skillToDraft(data);
        setDraft(loaded);
        setPristine(JSON.stringify(loaded));
        setLoadingSkill(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : String(err));
        setLoadingSkill(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isNew, skillId]);

  const onApplyProposal = useCallback((proposal: Proposal) => {
    setDraft((prev) => applyProposal(prev, proposal));
  }, []);

  const handleSave = useCallback(async () => {
    setSaveState({ status: "saving" });
    try {
      const body = buildSaveBody(draft, isNew);
      const path = isNew
        ? "/api/proxy/api/skills"
        : `/api/proxy/api/skills/${skillId}`;
      const res = await fetchWithAuth(path, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await safeErrorDetail(res);
        setSaveState({
          status: "error",
          message: `Save failed (HTTP ${res.status})${detail ? `: ${detail}` : ""}`,
        });
        return;
      }
      const saved = (await res.json()) as Skill;
      // Adopt the server's canonical draft (picks up generated skillId/slug on
      // create) so a subsequent Save is an update, not a duplicate create.
      const savedDraft = skillToDraft(saved);
      setDraft(savedDraft);
      setPristine(JSON.stringify(savedDraft));
      setSaveState({ status: "ok", message: "Saved." });
    } catch (err) {
      setSaveState({
        status: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, [draft, isNew, skillId]);

  const isDirty = useMemo(
    () => JSON.stringify(draft) !== pristine,
    [draft, pristine],
  );

  const handleCancel = useCallback(() => {
    if (isDirty && !window.confirm("Discard unsaved changes and leave the editor?")) {
      return;
    }
    router.back();
  }, [isDirty, router]);

  // Platform-owned skills are read-only unless you're a platform admin. Any
  // user can fork them into a private, editable copy.
  const isPlatformOwned = !isNew && draft.ownerId === PLATFORM_OWNER_UID;
  const [forking, setForking] = useState(false);

  const handleFork = useCallback(async () => {
    setForking(true);
    try {
      const res = await fetchWithAuth(`/api/proxy/api/skills/${skillId}/fork`, {
        method: "POST",
      });
      if (!res.ok) {
        const detail = await safeErrorDetail(res);
        setSaveState({
          status: "error",
          message: `Fork failed (HTTP ${res.status})${detail ? `: ${detail}` : ""}`,
        });
        return;
      }
      const forked = (await res.json()) as Skill;
      // Land in the editor for the new, owned copy.
      router.push(`/skills/studio/${forked.skillId}`);
    } catch (err) {
      setSaveState({
        status: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setForking(false);
    }
  }, [skillId, router]);

  // Offer Fork on platform skills, or after any save is rejected as read-only.
  const saveBlockedAsReadOnly =
    saveState.status === "error" && /fork/i.test(saveState.message);
  const showForkButton = isPlatformOwned || saveBlockedAsReadOnly;

  if (loadingSkill) {
    return <div className="p-6 text-sm text-muted-foreground">Loading skill…</div>;
  }
  if (loadError) {
    return (
      <main className="p-6">
        <p className="text-sm text-destructive">
          Could not load skill: {loadError}
        </p>
      </main>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h1 className="text-lg font-semibold">
            {isNew ? "New skill" : "Edit skill"}
          </h1>
          <p className="text-xs text-muted-foreground">
            {draft.displayName || draft.name || "Untitled skill"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {saveState.status === "ok" && (
            <span className="text-xs text-green-700">{saveState.message}</span>
          )}
          {saveState.status === "error" && (
            <span className="max-w-md truncate text-xs text-destructive">
              {saveState.message}
            </span>
          )}
          <button
            type="button"
            onClick={handleCancel}
            disabled={saveState.status === "saving" || forking}
            className="rounded-md border px-4 py-2 text-sm disabled:opacity-50"
          >
            Cancel
          </button>
          {showForkButton && (
            <button
              type="button"
              onClick={() => void handleFork()}
              disabled={forking}
              className={
                isPlatformOwned
                  ? "rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
                  : "rounded-md border px-4 py-2 text-sm disabled:opacity-50"
              }
              title="Make your own editable copy of this skill"
            >
              {forking ? "Forking…" : "Fork to customize"}
            </button>
          )}
          {/* Save stays visible for platform skills too — platform admins can
              save in place; non-admins get a 403 and use Fork instead. */}
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saveState.status === "saving" || !isDirty || forking}
            className={
              isPlatformOwned
                ? "rounded-md border px-4 py-2 text-sm disabled:opacity-50"
                : "rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50"
            }
          >
            {saveState.status === "saving" ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-2">
        <div className="min-h-0 overflow-auto border-r">
          {isPlatformOwned && (
            <div className="m-4 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
              This is a built-in <span className="font-medium text-foreground">platform skill</span>.
              It&apos;s read-only unless you&apos;re a platform admin — use{" "}
              <span className="font-medium text-foreground">Fork to customize</span> to make
              your own editable copy.
            </div>
          )}
          <BuilderForm draft={draft} setDraft={setDraft} isNew={isNew} />
        </div>
        <div className="min-h-0 overflow-hidden">
          <AGUIProvider skillId={AUTHORING_SKILL_ID} sessionId={seededThreadId}>
            <AuthoringCopilot
              skillId={skillId}
              onApplyProposal={onApplyProposal}
            />
          </AGUIProvider>
        </div>
      </div>
    </div>
  );
}

// === Builder form ===================================================

function BuilderForm({
  draft,
  setDraft,
  isNew,
}: {
  draft: StudioDraft;
  setDraft: React.Dispatch<React.SetStateAction<StudioDraft>>;
  isNew: boolean;
}) {
  const set = (patch: Partial<StudioDraft>) =>
    setDraft((prev) => ({ ...prev, ...patch }));
  const setMeta = (patch: Partial<NonNullable<StudioDraft["skillMetadata"]>>) =>
    setDraft((prev) => ({
      ...prev,
      skillMetadata: { ...prev.skillMetadata, ...patch },
    }));
  const setPersona = (patch: Partial<NonNullable<StudioDraft["persona"]>>) =>
    setDraft((prev) => ({ ...prev, persona: { ...prev.persona, ...patch } }));
  const setVoice = (
    patch: Partial<NonNullable<NonNullable<StudioDraft["persona"]>["voice"]>>,
  ) =>
    setDraft((prev) => ({
      ...prev,
      persona: {
        ...prev.persona,
        voice: { ...prev.persona?.voice, ...patch },
      },
    }));
  const setWelcome = (patch: Partial<NonNullable<StudioDraft["welcome"]>>) =>
    setDraft((prev) => ({ ...prev, welcome: { ...prev.welcome, ...patch } }));

  /** Patch `welcome.bucketBrowser` — the skill's document-library folder.
   *
   * `bucket` is deliberately NEVER set here. The skills API fills it per-request
   * from the viewer's own tenant `documents_bucket`, so an author picks a FOLDER
   * inside their own storage and cannot point a skill at another tenant's bucket
   * (attaching a bucket is an operator act — see issue #37). Clearing both fields
   * removes the browser entirely rather than mounting one at the bucket root. */
  const setLibrary = (patch: Partial<NonNullable<NonNullable<StudioDraft["welcome"]>["bucketBrowser"]>>) =>
    setDraft((prev) => {
      const next = { ...prev.welcome?.bucketBrowser, ...patch };
      const configured = Boolean(next.rootPath?.trim() || next.label?.trim());
      return {
        ...prev,
        welcome: { ...prev.welcome, bucketBrowser: configured ? next : null },
      };
    });

  // Default new skills to the most capable tier (smart → claude-opus). Existing
  // skills keep whatever tier they were saved with (including "pro" / raw ids).
  const model = draft.skillMetadata?.model ?? "smart";
  const tools = draft.skillMetadata?.tools ?? [];
  const persona = draft.persona ?? {};
  const voice = persona.voice ?? {};

  return (
    <form
      className="space-y-6 p-4"
      onSubmit={(e) => e.preventDefault()}
    >
      {isNew && (
        <Field label="Name (lowercase-kebab id)" hint="e.g. contract-reviewer">
          <input
            type="text"
            value={draft.name ?? ""}
            onChange={(e) => set({ name: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder="my-new-skill"
          />
        </Field>
      )}

      <Field label="Display name">
        <input
          type="text"
          value={draft.displayName ?? ""}
          onChange={(e) => set({ displayName: e.target.value })}
          className="w-full rounded-md border px-3 py-2 text-sm"
        />
      </Field>

      <Field label="Description">
        <input
          type="text"
          value={draft.description ?? ""}
          onChange={(e) => set({ description: e.target.value })}
          className="w-full rounded-md border px-3 py-2 text-sm"
        />
      </Field>

      <InstructionsEditor
        value={draft.instructions ?? ""}
        onChange={(v) => set({ instructions: v })}
      />

      <ModelTierPicker value={model} onChange={(tier) => setMeta({ model: tier })} />

      <CategoryPicker
        value={draft.skillMetadata?.category ?? ""}
        onChange={(next) => setMeta({ category: next || null })}
      />

      <ToolsPicker selected={tools} onChange={(next) => setMeta({ tools: next })} />

      <DelegationEditor
        value={draft.skillMetadata?.delegation}
        currentSkillId={draft.skillId}
        onChange={(next) => setMeta({ delegation: next })}
      />

      <AccessControlEditor
        value={draft.accessControl}
        onChange={(next) => set({ accessControl: next })}
      />

      <fieldset className="space-y-4 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Persona</legend>

        <AvatarPicker
          value={persona.avatar ?? ""}
          onChange={(v) => setPersona({ avatar: v })}
        />

        <Field label="Interaction style">
          <select
            value={persona.interactionStyle ?? "concise"}
            onChange={(e) =>
              setPersona({
                interactionStyle: e.target.value as InteractionStyle,
              })
            }
            className="w-full rounded-md border px-3 py-2 text-sm"
          >
            {INTERACTION_STYLES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Bio">
          <input
            type="text"
            value={persona.bio ?? ""}
            onChange={(e) => setPersona({ bio: e.target.value })}
            className="w-full rounded-md border px-3 py-2 text-sm"
          />
        </Field>

        <VoicePicker voice={voice} onChange={(patch) => setVoice(patch)} />
      </fieldset>

      <Field label="Welcome intro message">
        <textarea
          value={draft.welcome?.introMessage ?? ""}
          onChange={(e) => setWelcome({ introMessage: e.target.value })}
          rows={3}
          className="w-full rounded-md border px-3 py-2 text-sm"
        />
      </Field>

      <fieldset className="space-y-3 border-t pt-4">
        <legend className="text-sm font-medium">Document library</legend>
        <p className="text-xs text-muted-foreground">
          Show a folder from your organisation&apos;s document storage in this skill&apos;s sidebar. Give the
          folder path only — the storage account is resolved automatically, so a skill can only ever show
          your own organisation&apos;s documents.
        </p>

        <Field label="Folder path" hint="e.g. aitana3/PPAs/longform/ — leave blank for no library">
          <input
            type="text"
            value={draft.welcome?.bucketBrowser?.rootPath ?? ""}
            onChange={(e) => setLibrary({ rootPath: e.target.value })}
            placeholder="aitana3/PPAs/longform/"
            className="w-full rounded-md border px-3 py-2 text-sm"
          />
        </Field>

        <Field label="Library name" hint="Shown as the sidebar section heading">
          <input
            type="text"
            value={draft.welcome?.bucketBrowser?.label ?? ""}
            onChange={(e) => setLibrary({ label: e.target.value })}
            placeholder="Contracts library"
            className="w-full rounded-md border px-3 py-2 text-sm"
          />
        </Field>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.welcome?.bucketBrowser?.defaultOpen ?? false}
            onChange={(e) => setLibrary({ defaultOpen: e.target.checked })}
          />
          Open the library automatically
        </label>
      </fieldset>
    </form>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {hint && <span className="ml-2 text-xs text-muted-foreground">{hint}</span>}
      {children}
    </label>
  );
}

// === Category picker ================================================

/** v6.11.0 — sets `skillMetadata.category`, which groups this skill in the
 * top-nav skill dropdown (Specialists / Assistants / Tools). Presentation only:
 * it never affects access or delegation. Empty → the skill falls into the
 * dropdown's unlabelled/"Other skills" bucket. */
const CATEGORY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "", label: "Uncategorised" },
  { value: "assistant", label: "Assistant — general chat / front door" },
  { value: "specialist", label: "Specialist — domain expert" },
  { value: "tool", label: "Tool — narrow utility" },
];

function CategoryPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <Field label="Category" hint="Groups this skill in the top-nav dropdown (display only).">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border bg-background px-3 py-2 text-sm"
      >
        {CATEGORY_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

// === Avatar picker ==================================================

/** Gallery of curated default avatars + a manual URL fallback. Selecting a
 * thumbnail sets `persona.avatar` to its /public path. */
function AvatarPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div>
        <span className="text-sm font-medium">Avatar</span>
        <span className="ml-2 text-xs text-muted-foreground">
          Pick one, or paste an image URL below.
        </span>
      </div>
      <div className="flex flex-wrap gap-3">
        {DEFAULT_AVATARS.map((a) => {
          const selected = value === a.src;
          return (
            <button
              key={a.id}
              type="button"
              onClick={() => onChange(a.src)}
              title={a.label}
              aria-label={a.label}
              aria-pressed={selected}
              className={`h-16 w-16 overflow-hidden rounded-full border-2 transition ${
                selected
                  ? "border-primary ring-2 ring-primary"
                  : "border-transparent hover:border-muted-foreground/40"
              }`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={a.src}
                alt={a.label}
                className="h-full w-full object-cover"
              />
            </button>
          );
        })}
      </div>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border px-3 py-2 text-sm"
        placeholder="/images/avatars/… or https://…"
      />
    </div>
  );
}

// === Voice picker ===================================================

interface VoiceEntry {
  name: string;
  provider: string;
  tier: string;
  gender: string;
  label: string;
}
interface VoicesResponse {
  languages: string[];
  voices: Record<string, VoiceEntry[]>;
}

const LANG_NAMES: Record<string, string> = {
  es: "Spanish",
  en: "English (UK)",
  de: "German",
  fr: "French",
  it: "Italian",
  nl: "Dutch",
  da: "Danish",
};

/** Extract the Chirp3-HD persona name from a voice id, e.g.
 * "fr-FR-Chirp3-HD-Kore" → "Kore". Null for non-Chirp3-HD ids. */
function personaOf(voiceName?: string | null): string | null {
  if (!voiceName) return null;
  const m = voiceName.match(/-Chirp3-HD-(.+)$/);
  return m ? m[1] : null;
}

/**
 * Simple two-dropdown voice picker: Language + Voice. All voices are premium
 * Chirp3-HD personas that are identical across languages, so switching language
 * keeps the same persona. Writes ttsVoice + ttsProvider + language together.
 */
function VoicePicker({
  voice,
  onChange,
}: {
  voice: Partial<SkillVoiceConfig>;
  onChange: (patch: Partial<SkillVoiceConfig>) => void;
}) {
  const [data, setData] = useState<VoicesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/voice/voices")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: VoicesResponse) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const languages = data?.languages ?? [];
  const enabled = voice.enabled !== false; // default on

  // Which language is selected: explicit config → derived from the voice id →
  // first available.
  const langFromVoice = (() => {
    if (!voice.ttsVoice || !data) return null;
    return (
      data.languages.find((l) =>
        data.voices[l]?.some((v) => v.name === voice.ttsVoice),
      ) ?? null
    );
  })();
  const selectedLang =
    voice.language && languages.includes(voice.language)
      ? voice.language
      : (langFromVoice ?? languages[0] ?? "");

  const langVoices = data?.voices[selectedLang] ?? [];
  const persona = personaOf(voice.ttsVoice);
  const selectedVoiceName =
    langVoices.find((v) => v.name === voice.ttsVoice)?.name ??
    langVoices.find((v) => personaOf(v.name) === persona)?.name ??
    langVoices[0]?.name ??
    "";

  const applyVoice = (name: string, lang: string) => {
    const entry = data?.voices[lang]?.find((v) => v.name === name);
    onChange({
      ttsVoice: name,
      ttsProvider: entry?.provider ?? "gcp_chirp3hd",
      language: lang,
    });
  };

  const onLangChange = (lang: string) => {
    // Keep the same persona in the new language when possible.
    const p = personaOf(selectedVoiceName);
    const match =
      data?.voices[lang]?.find((v) => personaOf(v.name) === p) ??
      data?.voices[lang]?.[0];
    if (match) applyVoice(match.name, lang);
    else onChange({ language: lang });
  };

  return (
    <div className="space-y-2">
      <div>
        <span className="text-sm font-medium">Voice</span>
        <span className="ml-2 text-xs text-muted-foreground">
          Used for read-aloud. Premium voices — the same voice speaks any language.
        </span>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
        />
        <span>Enable read-aloud for this skill</span>
      </label>
      {error && (
        <p className="text-xs text-destructive">Could not load voices: {error}</p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Language">
          <select
            value={selectedLang}
            onChange={(e) => onLangChange(e.target.value)}
            disabled={!data || !enabled}
            className="w-full rounded-md border px-3 py-2 text-sm disabled:opacity-50"
          >
            {languages.map((l) => (
              <option key={l} value={l}>
                {LANG_NAMES[l] ?? l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Voice">
          <select
            value={selectedVoiceName}
            onChange={(e) => applyVoice(e.target.value, selectedLang)}
            disabled={!data || !enabled}
            className="w-full rounded-md border px-3 py-2 text-sm disabled:opacity-50"
          >
            {langVoices.map((v) => (
              <option key={v.name} value={v.name}>
                {v.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </div>
  );
}

// === Instructions editor (structured, guided) =======================

const INSTRUCTION_FIELDS: {
  key: Exclude<keyof StructuredInstructions, "additional">;
  label: string;
  hint: string;
  placeholder: string;
  rows: number;
}[] = [
  {
    key: "goal",
    label: "Goal",
    hint: "What is this skill's job?",
    placeholder: "e.g. Help users review and compare power-purchase agreements.",
    rows: 2,
  },
  {
    key: "guidelines",
    label: "Guidelines & behaviour",
    hint: "How should it act — tone, approach, what to prioritise?",
    placeholder:
      "e.g. Be precise and always cite the exact clause. Ask a clarifying question before guessing.",
    rows: 4,
  },
  {
    key: "constraints",
    label: "Constraints",
    hint: "Hard limits — what must it never do?",
    placeholder: "e.g. Never invent figures. Don't give legal advice.",
    rows: 3,
  },
  {
    key: "outputFormat",
    label: "Output format",
    hint: "How should answers be structured?",
    placeholder: "e.g. A short summary, then a bullet list of findings with citations.",
    rows: 2,
  },
];

/**
 * Guided instructions editor. Presents Goal / Guidelines / Constraints / Output
 * format as separate fields, combined into the single markdown `instructions`
 * string (the source of truth). Re-syncs when `value` changes from outside
 * (skill load or a copilot proposal). Legacy free-form instructions surface in
 * the "Additional" field so nothing is lost.
 */
function InstructionsEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const [fields, setFields] = useState<StructuredInstructions>(() =>
    parseInstructions(value),
  );
  const lastCombined = useRef(value);

  useEffect(() => {
    if (value !== lastCombined.current) {
      setFields(parseInstructions(value));
      lastCombined.current = value;
    }
  }, [value]);

  const update = (patch: Partial<StructuredInstructions>) => {
    const next = { ...fields, ...patch };
    setFields(next);
    const combined = combineInstructions(next);
    lastCombined.current = combined;
    onChange(combined);
  };

  return (
    <fieldset className="space-y-4 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Instructions</legend>
      <p className="text-xs text-muted-foreground">
        Fill in what you can — every section is optional. These are combined into
        the skill&apos;s full instructions automatically.
      </p>
      {INSTRUCTION_FIELDS.map((f) => (
        <Field key={f.key} label={f.label} hint={f.hint}>
          <textarea
            value={fields[f.key]}
            onChange={(e) =>
              update({ [f.key]: e.target.value } as Partial<StructuredInstructions>)
            }
            rows={f.rows}
            className="w-full rounded-md border px-3 py-2 text-sm"
            placeholder={f.placeholder}
          />
        </Field>
      ))}
      {fields.additional.trim() !== "" && (
        <Field
          label="Additional instructions"
          hint="Existing content that doesn't fit the sections above — kept as-is."
        >
          <textarea
            value={fields.additional}
            onChange={(e) => update({ additional: e.target.value })}
            rows={4}
            className="w-full rounded-md border px-3 py-2 font-mono text-sm"
          />
        </Field>
      )}
    </fieldset>
  );
}

// === Model tier picker ==============================================

interface ModelInfo {
  id: string;
  description?: string;
  provider?: string;
  tier?: string;
}
interface ModelsApiResponse {
  models: ModelInfo[];
  tier_defaults: Record<string, string>;
}

type TierOption = { tier: string; model: string; desc: string };

const TIER_LABELS: Record<string, { label: string; blurb: string }> = {
  smart: { label: "Best — most capable", blurb: "Highest quality, slower, most expensive." },
  pro: { label: "Pro — strong reasoning", blurb: "Great quality at a lower cost." },
  lite: { label: "Lite — fast & cheap", blurb: "Fastest and cheapest; good for simple skills." },
};
const TIER_ORDER = ["smart", "pro", "lite"];
const FALLBACK_TIERS: TierOption[] = [
  { tier: "smart", model: "claude-opus-4-8", desc: "" },
  { tier: "pro", model: "gemini-2.5-pro", desc: "" },
  { tier: "lite", model: "gemini-flash-lite", desc: "" },
];

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic — Claude",
  openai: "OpenAI — GPT",
  google: "Google — Gemini",
};
const PROVIDER_ORDER = ["anthropic", "openai", "google"];

/** Language-model picker, sourced from GET /api/models. Offers the logical tiers
 * (residency-aware, recommended) PLUS every specific model grouped by provider,
 * so a skill can pin an exact model (e.g. gpt-5-6-sol, claude-opus-4-8). */
function ModelTierPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (tier: string) => void;
}) {
  const [tiers, setTiers] = useState<TierOption[] | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/models")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: ModelsApiResponse) => {
        if (cancelled) return;
        const byId = new Map(data.models.map((m) => [m.id, m.description ?? m.id]));
        const list = Object.entries(data.tier_defaults ?? {}).map(([tier, id]) => ({
          tier,
          model: id,
          desc: byId.get(id) ?? id,
        }));
        list.sort(
          (a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier),
        );
        setTiers(list.length > 0 ? list : FALLBACK_TIERS);
        setModels(data.models ?? []);
      })
      .catch(() => {
        if (!cancelled) setTiers(FALLBACK_TIERS);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tierList = tiers ?? FALLBACK_TIERS;
  const byProvider: Record<string, ModelInfo[]> = {};
  for (const m of models) {
    const p = m.provider ?? "other";
    (byProvider[p] ??= []).push(m);
  }
  // Keep a saved tier/id that isn't in either list selectable, so opening the
  // form never silently downgrades the model.
  const known =
    tierList.some((t) => t.tier === value) || models.some((m) => m.id === value);
  const currentTier = tierList.find((t) => t.tier === value);

  return (
    <Field
      label="Language model"
      hint="A tier picks a residency-appropriate model automatically; a specific model pins it."
    >
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border px-3 py-2 text-sm"
      >
        <optgroup label="Tiers (recommended)">
          {tierList.map((t) => (
            <option key={t.tier} value={t.tier}>
              {TIER_LABELS[t.tier]?.label ?? t.tier} · {t.model}
            </option>
          ))}
        </optgroup>
        {PROVIDER_ORDER.filter((p) => byProvider[p]?.length).map((p) => (
          <optgroup key={p} label={PROVIDER_LABELS[p] ?? p}>
            {byProvider[p].map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.description ? ` — ${m.description}` : ""}
              </option>
            ))}
          </optgroup>
        ))}
        {!known && value ? <option value={value}>{value}</option> : null}
      </select>
      <span className="mt-1 block text-xs text-muted-foreground">
        {currentTier
          ? TIER_LABELS[currentTier.tier]?.blurb ?? currentTier.desc
          : "Pinned model — won't run under a stricter residency policy (eu-strict)."}
      </span>
    </Field>
  );
}

// === Tools picker ===================================================

interface ToolInfo {
  name: string;
  label: string;
  description: string;
  category: string;
}

/** Tool checklist, sourced from GET /api/tools. Replaces the old
 * comma-separated free-text field. Any selected tool not in the catalog
 * (e.g. advanced/MCP tools) is still shown checked so it isn't dropped. */
function ToolsPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [catalog, setCatalog] = useState<ToolInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchWithAuth("/api/proxy/api/tools")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: { tools: ToolInfo[] }) => {
        if (!cancelled) setCatalog(data.tools ?? []);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (name: string, on: boolean) => {
    onChange(
      on ? [...selected.filter((t) => t !== name), name] : selected.filter((t) => t !== name),
    );
  };

  // Group catalog tools by category, preserving first-seen order.
  const groups: { category: string; tools: ToolInfo[] }[] = [];
  for (const t of catalog ?? []) {
    let g = groups.find((x) => x.category === t.category);
    if (!g) {
      g = { category: t.category, tools: [] };
      groups.push(g);
    }
    g.tools.push(t);
  }
  const catalogNames = new Set((catalog ?? []).map((t) => t.name));
  const extras = selected.filter((n) => !catalogNames.has(n));

  return (
    <fieldset className="space-y-3 rounded-md border p-3">
      <legend className="px-1 text-sm font-medium">Tools</legend>
      <p className="text-xs text-muted-foreground">
        Choose the capabilities this skill can use.
      </p>

      {catalog === null && !error && (
        <p className="text-xs text-muted-foreground">Loading tools…</p>
      )}
      {error && (
        <p className="text-xs text-destructive">Could not load tools: {error}</p>
      )}

      {groups.map((g) => (
        <div key={g.category} className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {g.category}
          </p>
          {g.tools.map((t) => (
            <label key={t.name} className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={selected.includes(t.name)}
                onChange={(e) => toggle(t.name, e.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="text-sm font-medium">{t.label}</span>
                <span className="block text-xs text-muted-foreground">
                  {t.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      ))}

      {extras.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Advanced
          </p>
          {extras.map((name) => (
            <label key={name} className="flex items-start gap-2">
              <input
                type="checkbox"
                checked
                onChange={() => toggle(name, false)}
                className="mt-1"
              />
              <span className="font-mono text-sm">{name}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  );
}

// === Draft <-> wire helpers =========================================

function emptyDraft(): StudioDraft {
  return {
    name: "",
    displayName: "",
    description: "",
    instructions: "",
    // Default to the most capable tier (smart → claude-opus).
    skillMetadata: { model: "smart", tools: [], subSkills: [], toolConfigs: {} },
    persona: { avatar: randomGlyphAvatar(), interactionStyle: "concise", voice: {} },
    welcome: {},
  };
}

function skillToDraft(s: Skill): StudioDraft {
  return {
    skillId: s.skillId,
    ownerId: s.ownerId,
    name: s.name,
    displayName: s.displayName,
    description: s.description,
    instructions: s.instructions,
    avatar: s.avatar,
    skillMetadata: {
      model: s.skillMetadata?.model,
      tools: s.skillMetadata?.tools ?? [],
      subSkills: s.skillMetadata?.subSkills ?? [],
      toolConfigs: s.skillMetadata?.toolConfigs ?? {},
      ...(s.skillMetadata?.delegation ? { delegation: s.skillMetadata.delegation } : {}),
      // Carry the dropdown-grouping category through so a Save doesn't wipe it.
      ...(s.skillMetadata?.category ? { category: s.skillMetadata.category } : {}),
      // Carry the confirmation-tool opt-out through (front door sets false) so a
      // Studio Save doesn't silently re-enable request_confirmation.
      ...(s.skillMetadata?.enableConfirmation === false ? { enableConfirmation: false } : {}),
    },
    persona: s.persona ?? { interactionStyle: "concise" },
    welcome: s.welcome ?? {},
    accessControl: s.accessControl as unknown as Record<string, unknown>,
  };
}

/**
 * Build the request body for Save. Sends the whole draft. On create the backend
 * requires `name`; on update `name` is not accepted (routes.py UpdateSkillRequest)
 * so we omit it. camelCase keys match the request-model aliases.
 */
function buildSaveBody(draft: StudioDraft, isNew: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    description: draft.description ?? "",
    instructions: draft.instructions ?? "",
    displayName: draft.displayName ?? "",
    skillMetadata: draft.skillMetadata ?? {},
    persona: draft.persona ?? {},
    welcome: draft.welcome ?? {},
    // 9.2: access control is now editable + saved (was omitted, so skills were
    // stuck private). The backend PUT/POST accept accessControl.
    accessControl: draft.accessControl ?? { type: "private" },
  };
  if (isNew) {
    body.name = draft.name ?? "";
  }
  return body;
}

async function safeErrorDetail(res: Response): Promise<string | null> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail) return JSON.stringify(data.detail);
  } catch {
    // non-JSON body — no detail
  }
  return null;
}
