"use client";

// v6.34.0 DOC-WORKBENCH P3 — "the a2ui workbench element could also help with
// formatting, both native and letting users upload template docs".
//
// Restyling used to require describing it in prose to the assistant, and the
// template had to have been uploaded somewhere else first. This puts both on the
// surface: pick a template you already have, upload a new one without leaving,
// or use the native knobs that need no template at all.
//
// Two things it deliberately does NOT do:
//
//   * It does not invent a second generation path. Every control POSTs to
//     `/{doc_id}/revise`, which shares one implementation with `/generate` —
//     the same rule the v6.32.0 ingest extraction exists to enforce.
//   * It does not send a doc-id. Templates are chosen by FILE NAME and the
//     backend resolves it (CLAUDE.md #9); neither the user nor the model ever
//     handles an opaque id.
//
// Restyling costs no model call: the stored source markdown is re-rendered
// through the deterministic converter.

import { useCallback, useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/apiClient";
import { reportDocumentAction, type DocumentActivitySink } from "@/lib/documentActivity";
import type { DocumentTemplate } from "@/hooks/useDocument";

interface TemplateOption {
  filename: string;
  source?: string;
}

/** What the backend reports back about the restyle it just performed. */
interface RestyleOutcome {
  templateUsed?: string;
  referenceDocApplied?: boolean;
  templatePartsCarried?: number;
}

interface DocumentFormattingProps {
  docId: string;
  /**
   * The template this document is currently styled from, from its own record.
   *
   * The panel used to be stateless about the document: it opened on "Plain (no
   * template)" no matter what had been applied, and reset to it after every
   * Apply, so a successful restyle read as one that had not happened. The
   * selection is a property of the DOCUMENT, not of this component's lifetime.
   */
  currentTemplate?: DocumentTemplate | null;
  /** Called after a successful restyle so the viewer can reload the document. */
  onRevised?: () => void;
  /** Reports the restyle into the Activity feed. */
  activitySink?: DocumentActivitySink;
}

// `referenceDoc` is DOCX-only upstream — no other format has a styling
// container — so a template picker alongside a .pptx target would be a control
// that cannot work. Verified live: target=pptx + referenceDoc returns
// REFERENCE_DOC_NOT_APPLICABLE.
const TEMPLATABLE_TARGET = "docx";

export function DocumentFormatting({
  docId,
  currentTemplate,
  onRevised,
  activitySink,
}: DocumentFormattingProps) {
  const [templates, setTemplates] = useState<TemplateOption[]>([]);
  const [template, setTemplate] = useState(currentTemplate?.filename ?? "");
  const [tableStyle, setTableStyle] = useState("");
  const [referenceSection, setReferenceSection] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<RestyleOutcome | null>(null);

  // Follow the document. After an Apply the record updates in place and this
  // re-syncs the picker to what was actually stored, rather than to whatever
  // was typed into it.
  const appliedFilename = currentTemplate?.filename ?? "";
  useEffect(() => {
    setTemplate(appliedFilename);
  }, [docId, appliedFilename]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchWithAuth("/api/proxy/api/documents/templates");
        if (cancelled || !res.ok) return;
        const body = (await res.json()) as { templates: TemplateOption[] };
        if (!cancelled) setTemplates(body.templates ?? []);
      } catch {
        // A template list that fails to load leaves the native controls usable;
        // the Apply path reports its own errors.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleUploadTemplate = useCallback(async (file: File) => {
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetchWithAuth("/api/proxy/api/documents/upload", { method: "POST", body: form });
      if (!res.ok) {
        setError(`Couldn't upload the template (${res.status})`);
        return;
      }
      const doc = (await res.json()) as { originalFilename?: string };
      const name = doc.originalFilename ?? file.name;
      // An uploaded template is a workspace document like any other — it goes
      // through the ordinary upload path, not a side door.
      setTemplates((prev) => [...prev, { filename: name }]);
      setTemplate(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't upload the template");
    }
  }, []);

  const handleApply = useCallback(async () => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    const settle = reportDocumentAction(activitySink, "restyle_document", template ? `template: ${template}` : "plain styling");
    try {
      const body: Record<string, unknown> = { target: TEMPLATABLE_TARGET };
      if (template) body.template = template;
      if (tableStyle.trim()) body.tableStyle = tableStyle.trim();
      const section = Number.parseInt(referenceSection, 10);
      if (!Number.isNaN(section)) body.referenceSection = section;

      const res = await fetchWithAuth(`/api/proxy/api/documents/${encodeURIComponent(docId)}/revise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        // NEVER-SILENT #8. The backend's typed errors are worth showing
        // verbatim — TEMPLATE_NOT_FOUND and NO_EDITABLE_SOURCE both tell the
        // user something they can act on.
        const detail = await res.json().catch(() => null);
        const message =
          (detail?.detail?.message as string | undefined) ??
          (typeof detail?.detail === "string" ? detail.detail : null) ??
          `Restyle failed (${res.status})`;
        setError(message);
        settle({ ok: false, message });
        return;
      }
      // NEVER-SILENT #8: "Updated" was true whether the template had carried
      // twelve parts or none, which is exactly the ambiguity that made a
      // working restyle indistinguishable from a no-op. Report what the
      // converter actually did.
      const result = (await res.json().catch(() => null)) as RestyleOutcome | null;
      setOutcome(result ?? {});
      settle({
        ok: true,
        message: template
          ? `Restyled from ${template} (${result?.templatePartsCarried ?? 0} template parts carried)`
          : "Restyled without a template",
      });
      onRevised?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Restyle failed";
      setError(message);
      settle({ ok: false, message });
    } finally {
      setBusy(false);
    }
  }, [docId, template, tableStyle, referenceSection, onRevised, activitySink]);

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Formatting</p>
      <p className="text-[10px] leading-snug text-muted-foreground">
        Re-render this document using another document&apos;s look — its fonts, colours, heading
        styles, headers and footers. The words stay the same.
      </p>

      {/* What this document is styled from RIGHT NOW, read from its own record.
          Without this the panel could only ever describe an intention, never a
          state — so there was no way to tell an applied template from a
          forgotten one. */}
      <p className="text-[10px] leading-snug" data-testid="current-template">
        {currentTemplate ? (
          !currentTemplate.filename ? (
            // Styled before the template's name was recorded. Saying "no
            // template" here would be a lie about the document.
            <span className="text-muted-foreground">
              Styled from a template, but its name wasn&apos;t recorded. Apply one again to see it
              named here.
            </span>
          ) : currentTemplate.applied ? (
            <span className="text-muted-foreground">
              Currently styled from{" "}
              <span className="font-medium text-foreground">{currentTemplate.filename}</span>
              {currentTemplate.partsCarried > 0
                ? ` — ${currentTemplate.partsCarried} template parts carried.`
                : " — the template applied but had no styling parts to carry."}
            </span>
          ) : (
            <span className="text-amber-600">
              {currentTemplate.filename} was selected, but the converter did not apply it.
            </span>
          )
        ) : (
          <span className="text-muted-foreground">Not styled from a template.</span>
        )}
      </p>

      <label className="flex items-center gap-2 text-[11px]">
        <span className="w-24 shrink-0 text-muted-foreground">Copy look from</span>
        <select
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          aria-label="Template"
          className="min-w-0 flex-1 rounded border bg-background px-1 py-0.5 text-[11px]"
        >
          <option value="">Plain (no template)</option>
          {templates.map((t) => (
            <option key={t.filename} value={t.filename}>
              {t.filename}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-[11px]">
        <span className="w-24 shrink-0 text-muted-foreground">…or upload one</span>
        <input
          type="file"
          accept=".docx"
          aria-label="Upload a template"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleUploadTemplate(file);
          }}
          className="min-w-0 flex-1 text-[10px] file:mr-2 file:rounded file:border-0 file:bg-accent file:px-1.5 file:py-0.5 file:text-[10px]"
        />
      </label>

      {/* The two expert knobs. They were labelled "Table style" and "Section",
          which told nobody anything — both select WITHIN a template, and neither
          is needed for the common case of "make it look like this". Folded away
          with an explanation of what they actually do. */}
      <details className="text-[11px]">
        <summary className="cursor-pointer text-[10px] text-muted-foreground hover:text-foreground">
          More options
        </summary>
        <div className="mt-1.5 flex flex-col gap-1.5 border-l pl-2">
          <label className="flex items-start gap-2">
            <span className="w-24 shrink-0 pt-1 text-muted-foreground">Table look</span>
            <span className="min-w-0 flex-1">
              <input
                type="text"
                value={tableStyle}
                onChange={(e) => setTableStyle(e.target.value)}
                placeholder="e.g. Table Grid"
                aria-label="Table look"
                disabled={!template}
                className="w-full rounded border bg-background px-1 py-0.5 text-[11px] disabled:opacity-50"
              />
              <span className="mt-0.5 block text-[10px] text-muted-foreground">
                Name of a table style in the template to use for this document&apos;s tables. Leave
                blank to use the template&apos;s default.
              </span>
            </span>
          </label>

          <label className="flex items-start gap-2">
            <span className="w-24 shrink-0 pt-1 text-muted-foreground">Page setup</span>
            <span className="min-w-0 flex-1">
              <input
                type="number"
                min={1}
                value={referenceSection}
                onChange={(e) => setReferenceSection(e.target.value)}
                placeholder="last"
                aria-label="Page setup section"
                disabled={!template}
                className="w-20 rounded border bg-background px-1 py-0.5 text-[11px] disabled:opacity-50"
              />
              <span className="mt-0.5 block text-[10px] text-muted-foreground">
                Which part of the template supplies the margins, headers and footers. Most templates
                have one — leave blank.
              </span>
            </span>
          </label>
        </div>
      </details>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleApply}
          disabled={busy}
          className="rounded border px-2 py-0.5 text-[11px] hover:bg-accent disabled:opacity-50"
        >
          {busy ? "Applying…" : "Apply formatting"}
        </button>
        {outcome && !error && (
          <span className="text-[10px] text-emerald-600" role="status">
            {outcome.templateUsed
              ? outcome.referenceDocApplied
                ? `Applied ${outcome.templateUsed} — ${outcome.templatePartsCarried ?? 0} parts carried`
                : `${outcome.templateUsed} did not apply`
              : "Restyled without a template"}
          </span>
        )}
      </div>

      {error && (
        <p className="text-[10px] text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
