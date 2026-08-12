// Aitana custom icon set.
//
// Brand glyphs drawn in the Aitana house style (see ./README.md). Each is a
// thin wrapper over <Icon>, so they inherit the shared 24-grid / 1.75-stroke /
// round-join / currentColor conventions. Use `size` and `className` per icon:
//
//   import { SkillIcon } from "@/components/icons";
//   <SkillIcon className="text-primary" size={18} />
//
// For everything not here, keep using lucide-react — it already matches this
// style. Add a new glyph only when lucide lacks a good brand-fit.

import { Icon, type IconProps } from "./Icon";

export { Icon, type IconProps };

/** Skill / bundle — stacked layers (a skill packages tools + persona). */
export function SkillIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3 21 8 12 13 3 8Z" />
      <path d="M4 12 12 16.5 20 12" />
      <path d="M4 16 12 20.5 20 16" />
    </Icon>
  );
}

/** Document with folded corner + text lines. */
export function DocIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9Z" />
      <path d="M13 3v6h6" />
      <path d="M9 13h6" />
      <path d="M9 17h4" />
    </Icon>
  );
}

/** Compare — two side-by-side panels. */
export function CompareIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="7.5" height="16" rx="1.5" />
      <rect x="13.5" y="4" width="7.5" height="16" rx="1.5" />
    </Icon>
  );
}

/** Search — magnifier. */
export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" />
    </Icon>
  );
}

/** Voice / read-aloud — speaker with sound waves. */
export function VoiceIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 9v6h3.5L13 19V5L7.5 9Z" />
      <path d="M16.5 8.5a5 5 0 0 1 0 7" />
      <path d="M19 6a8 8 0 0 1 0 12" />
    </Icon>
  );
}

/** Send — paper plane. */
export function SendIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M22 2 11 13" />
      <path d="M22 2 15 22l-4-9-9-4Z" />
    </Icon>
  );
}

/** Settings — sliders (softer than a gear, matches the rounded house style). */
export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16" />
      <path d="M4 17h16" />
      <circle cx="9" cy="7" r="2.2" />
      <circle cx="15" cy="17" r="2.2" />
    </Icon>
  );
}

/** Sparkle — AI / authoring / magic. */
export function SparkleIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3c.6 4.2 1.8 5.4 6 6-4.2.6-5.4 1.8-6 6-.6-4.2-1.8-5.4-6-6 4.2-.6 5.4-1.8 6-6Z" />
      <path d="M19 3.2c.2 1.2.6 1.6 1.8 1.8-1.2.2-1.6.6-1.8 1.8-.2-1.2-.6-1.6-1.8-1.8 1.2-.2 1.6-.6 1.8-1.8Z" />
    </Icon>
  );
}
