// Default avatar gallery for the Skill Studio ("Create a new skill").
//
// One cohesive set of 3D icons in the template's house style (the Sunholo
// brand's service-icon renders: a matte clay object on a slate plinth,
// slate/coral/teal/off-white, on white). Each names a KIND of skill, not a
// specific one, so any new skill finds a fitting icon. Selecting one sets
// `persona.avatar` to its `src` (a /public path served by Next).
//
// Adding one: see frontend/public/images/avatars/README.md for the style spec
// and how they are generated, drop `<id>.webp` in public/images/avatars/
// gallery/, and append an entry here. Keep the set consistent — a matched set
// looks intentional; a grab-bag looks cheap.
//
// The previous set (flat portrait JPGs and coloured-circle glyph SVGs in
// public/images/avatars/) is no longer offered, but the files stay: existing
// skills, template SKILL.md frontmatter and forks' stored skills point at them.

export interface DefaultAvatar {
  id: string;
  label: string;
  src: string;
}

const gallery = (id: string, label: string): DefaultAvatar => ({
  id,
  label,
  src: `/images/avatars/gallery/${id}.webp`,
});

export const DEFAULT_AVATARS: DefaultAvatar[] = [
  gallery("assistant", "Assistant"),
  gallery("research", "Research"),
  gallery("knowledge", "Knowledge search"),
  gallery("documents", "Documents"),
  gallery("writing", "Writing"),
  gallery("data", "Data extraction"),
  gallery("analytics", "Analytics & costs"),
  gallery("dashboard", "Dashboard"),
  gallery("code", "Engineering"),
  gallery("language", "Programming language"),
  gallery("builder", "Builder"),
  gallery("browser", "Web browsing"),
  gallery("cloud", "Cloud"),
  gallery("maps", "Maps & places"),
  gallery("reasoning", "Reasoning"),
  gallery("orchestrator", "Orchestrator"),
  gallery("ideas", "Ideas"),
  gallery("learning", "Learning"),
  gallery("translation", "Translation"),
  gallery("email", "Email"),
  gallery("scheduling", "Scheduling"),
  gallery("legal", "Legal"),
  gallery("security", "Security"),
  gallery("lab", "Testing"),
];

/** A random gallery icon for a new skill that hasn't picked one yet. */
export function randomDefaultAvatar(): string {
  return DEFAULT_AVATARS[Math.floor(Math.random() * DEFAULT_AVATARS.length)]!.src;
}
