// Default avatar gallery for the skill editor.
//
// A small curated set of professional, cohesive flat-vector portrait avatars
// plus per-skill glyph avatars (coloured circle + domain icon). The picker in
// the Skill Studio renders these; selecting one sets `persona.avatar` to its
// `src` (a /public path served by Next).
//
// To add more portraits: generate a matching image (see
// frontend/public/images/avatars/README.md for the exact style spec), drop it
// in frontend/public/images/avatars/, and append an entry here. Keep the set
// stylistically consistent — that's what makes the gallery look intentional.
//
// Glyphs are SVG circles with a domain icon; they scale perfectly and are
// served from the same directory. The colour palette is brand-consistent and
// distinct enough to tell skills apart in the switcher at 20 px.

export interface DefaultAvatar {
  id: string;
  label: string;
  src: string;
}

// Portrait avatars — head & shoulders, flat-vector illustration style.
export const PORTRAIT_AVATARS: DefaultAvatar[] = [
  { id: "ana", label: "Ana", src: "/images/avatars/ana.jpg" },
  { id: "marco", label: "Marco", src: "/images/avatars/marco.jpg" },
];

// Glyph avatars — coloured circle + stroke icon, one per built-in skill.
// Also available as user picks in the Studio gallery.
export const GLYPH_AVATARS: DefaultAvatar[] = [
  { id: "skill-ppa-expert", label: "PPA Expert", src: "/images/avatars/skill-ppa-expert.svg" },
  { id: "skill-doc-compare", label: "Doc Compare", src: "/images/avatars/skill-doc-compare.svg" },
  { id: "skill-general-assistant", label: "General Assistant", src: "/images/avatars/skill-general-assistant.svg" },
  { id: "skill-document-analyst", label: "Document Analyst", src: "/images/avatars/skill-document-analyst.svg" },
  { id: "skill-web-researcher", label: "Web Researcher", src: "/images/avatars/skill-web-researcher.svg" },
  { id: "skill-data-extractor", label: "Data Extractor", src: "/images/avatars/skill-data-extractor.svg" },
  { id: "skill-code-assistant", label: "Code Assistant", src: "/images/avatars/skill-code-assistant.svg" },
  { id: "skill-authoring-assistant", label: "Authoring Assistant", src: "/images/avatars/skill-authoring-assistant.svg" },
  { id: "skill-workspace-demo", label: "Workspace Demo", src: "/images/avatars/skill-workspace-demo.svg" },
  { id: "skill-workspace-demo-interactive", label: "Interactive Demo", src: "/images/avatars/skill-workspace-demo-interactive.svg" },
];

export const DEFAULT_AVATARS: DefaultAvatar[] = [...PORTRAIT_AVATARS, ...GLYPH_AVATARS];

/** Pick a random glyph avatar src for new skills that haven't been assigned one. */
export function randomGlyphAvatar(): string {
  return GLYPH_AVATARS[Math.floor(Math.random() * GLYPH_AVATARS.length)]!.src;
}
