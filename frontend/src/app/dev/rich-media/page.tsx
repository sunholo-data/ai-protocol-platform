/**
 * Dev fixture page — Rich Media Rendering review
 *
 * Renders ChatMessageList with pre-built messages covering all three
 * rendering paths introduced in the RICH-MEDIA sprint:
 *
 *   1. SVG     — ```svg fenced block → SVGBlock (DOMPurify sanitized)
 *   2. Image   — ![alt](url) → InlineImage (lazy, lightbox, error fallback)
 *   3. PDF     — [text](*.pdf) → PDFCard (filename + page count chip)
 *
 * Accessible at http://localhost:3456/dev/rich-media (dev only — no auth).
 * No backend required; all fixture data is hard-coded here.
 */

"use client";

import { ChatMessageList } from "@/components/chat/ChatMessageList";
import type { SkillMessage } from "@/hooks/useSkillAgent";
import { seedPDFInfoCache } from "@/hooks/usePDFInfo";

// ---------------------------------------------------------------------------
// Fixture SVG — AG-UI protocol flow diagram
// ---------------------------------------------------------------------------

const ARCH_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580 200">
  <defs>
    <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
      <polygon points="0 0, 8 3, 0 6" fill="#9ca3af"/>
    </marker>
  </defs>

  <rect width="580" height="200" rx="8" fill="#f9fafb"/>

  <text x="290" y="28" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="13" font-weight="600" fill="#111827">AG-UI Protocol Flow</text>
  <text x="290" y="46" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="10" fill="#6b7280">Each user turn streams AG-UI events from backend to chat UI</text>

  <!-- ADK Agent -->
  <rect x="20" y="75" width="100" height="50" rx="6" fill="#fed7aa" stroke="#f97316" stroke-width="1.5"/>
  <text x="70" y="97" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="11" font-weight="600" fill="#7c2d12">ADK</text>
  <text x="70" y="112" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="10" fill="#9a3412">Agent</text>

  <!-- Arrow 1 -->
  <line x1="120" y1="100" x2="155" y2="100" stroke="#9ca3af" stroke-width="1.5" marker-end="url(#arrow)"/>
  <text x="137" y="93" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="9" fill="#6b7280">SSE</text>

  <!-- FastAPI -->
  <rect x="155" y="75" width="110" height="50" rx="6" fill="#bbf7d0" stroke="#16a34a" stroke-width="1.5"/>
  <text x="210" y="97" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="11" font-weight="600" fill="#14532d">FastAPI</text>
  <text x="210" y="112" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="10" fill="#166534">/api/skill/stream</text>

  <!-- Arrow 2 -->
  <line x1="265" y1="100" x2="300" y2="100" stroke="#9ca3af" stroke-width="1.5" marker-end="url(#arrow)"/>
  <text x="282" y="93" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="9" fill="#6b7280">Events</text>

  <!-- HttpAgent -->
  <rect x="300" y="75" width="120" height="50" rx="6" fill="#bfdbfe" stroke="#2563eb" stroke-width="1.5"/>
  <text x="360" y="97" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="11" font-weight="600" fill="#1e3a8a">HttpAgent</text>
  <text x="360" y="112" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="10" fill="#1d4ed8">useSkillAgent</text>

  <!-- Arrow 3 -->
  <line x1="420" y1="100" x2="455" y2="100" stroke="#9ca3af" stroke-width="1.5" marker-end="url(#arrow)"/>
  <text x="437" y="93" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="9" fill="#6b7280">State</text>

  <!-- Chat UI -->
  <rect x="455" y="75" width="105" height="50" rx="6" fill="#fde68a" stroke="#d97706" stroke-width="1.5"/>
  <text x="507" y="97" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="11" font-weight="600" fill="#78350f">Chat UI</text>
  <text x="507" y="112" text-anchor="middle" font-family="ui-sans-serif,sans-serif" font-size="10" fill="#92400e">MessageBubble</text>

  <!-- Event legend -->
  <text x="20" y="160" font-family="ui-mono,monospace" font-size="9" fill="#6b7280">TEXT_MESSAGE_START → CONTENT → END   TOOL_CALL_START → END</text>
  <text x="20" y="175" font-family="ui-mono,monospace" font-size="9" fill="#6b7280">RUN_STARTED                           RUN_FINISHED / RUN_ERROR</text>
</svg>`;

// ---------------------------------------------------------------------------
// PDF fixture — public GCS URL, page count pre-seeded so no backend needed.
// ---------------------------------------------------------------------------

const PDF_URL =
  "https://storage.googleapis.com/aitana-public-bucket/press-release/AitanaIncorporation.pdf";
seedPDFInfoCache(PDF_URL, { filename: "AitanaIncorporation.pdf", pages: 2 });

// ---------------------------------------------------------------------------
// Fixture messages
// ---------------------------------------------------------------------------

const MESSAGES: SkillMessage[] = [
  {
    id: "msg-1",
    role: "user",
    content: "Can you show me the AG-UI streaming architecture diagram?",
  },
  {
    id: "msg-2",
    role: "assistant",
    content: [
      "Here's the AG-UI protocol flow — this is the backbone of how every",
      "user message reaches the chat UI and back.",
      "",
      "```svg",
      ARCH_SVG,
      "```",
      "",
      "Each user turn opens a new SSE connection to `/api/skill/{id}/stream`.",
      "The ADK agent emits AG-UI events, `HttpAgent` translates them into React",
      "state, and `ChatMessageList` renders them as `MessageBubble` components.",
    ].join("\n"),
  },
  {
    id: "msg-3",
    role: "user",
    content:
      "Great. What does the document workspace look like? And can you share the incorporation document?",
  },
  {
    id: "msg-4",
    role: "assistant",
    content: [
      "Here's a screenshot of the document analysis workspace:",
      "",
      "![Aitana document workspace](https://picsum.photos/seed/aitana-workspace/640/280)",
      "",
      "The workspace shows the folder browser on the left, the A2UI document",
      "renderer in the centre, and the chat panel on the right.",
      "",
      "The Aitana incorporation document referenced in the analysis is here — click to open:",
      "",
      `[Aitana Incorporation](${PDF_URL})`,
      "",
      "Click the card to open the PDF in your browser.",
    ].join("\n"),
  },
  {
    id: "msg-5",
    role: "user",
    content: "Does it handle broken images and malformed SVG?",
  },
  {
    id: "msg-6",
    role: "assistant",
    content: [
      "Yes — all three paths have explicit fallbacks:",
      "",
      "**Broken image** (this URL does not exist):",
      "",
      "![missing photo](https://storage.googleapis.com/platform-documents-demo/does-not-exist.png)",
      "",
      "The broken-image icon + alt text appears instead of a missing-resource box.",
      "",
      "**SVG with injected script** (DOMPurify strips it before render):",
      "",
      "```svg",
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 80">',
      "  <script>alert('xss')</script>",
      '  <rect width="200" height="80" rx="6" fill="#fecaca" stroke="#ef4444"/>',
      '  <text x="100" y="32" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#7f1d1d">DOMPurify removed the \\u003cscript\\u003e tag.</text>',
      '  <text x="100" y="52" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#991b1b">Only the rect and text survived sanitization.</text>',
      "</svg>",
      "```",
      "",
      "The `<script>` tag is stripped; only safe SVG elements render.",
    ].join("\n"),
  },
];

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const NOOP = () => {};

export default function RichMediaDevPage() {
  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b border-border bg-muted/30 px-6 py-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-foreground">Rich Media Rendering — Dev Fixture</h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              SVG · InlineImage · PDFCard · error fallbacks · DOMPurify XSS strip
            </p>
          </div>
          <span className="rounded border border-orange-300 bg-orange-50 px-2 py-0.5 text-xs font-medium text-orange-700">
            DEV ONLY
          </span>
        </div>
      </div>

      {/* Chat fixture — same layout as the real chat panel */}
      <div className="mx-auto max-w-3xl" style={{ height: "calc(100vh - 60px)" }}>
        <ChatMessageList
          messages={MESSAGES}
          toolCalls={[]}
          thinkingContent=""
          isThinking={false}
          isLoading={false}
          error={null}
          skillId="doc-analyst"
          userInitial="M"
          userDisplayName="Mark"
          activeDocumentContext={{ folderName: "Q1 Financial Review", docCount: 14 }}
          navigateToBlock={NOOP}
          onAction={NOOP}
        />
      </div>
    </div>
  );
}
