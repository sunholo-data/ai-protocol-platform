# Aitana icons

Custom brand glyphs in the **Aitana house style**. For standard UI glyphs, keep
using [`lucide-react`](https://lucide.dev) — it already matches this style. Only
add a custom icon here when lucide lacks a good brand fit.

## House style

All icons render through `<Icon>` ([Icon.tsx](./Icon.tsx)), which locks:

| Property        | Value                          |
| --------------- | ------------------------------ |
| `viewBox`       | `0 0 24 24`                    |
| `stroke`        | `currentColor` (never a fixed colour) |
| `fill`          | `none` (outline icons)         |
| `strokeWidth`   | `1.75`                         |
| `strokeLinecap` | `round`                        |
| `strokeLinejoin`| `round`                        |
| default `size`  | `20` (px)                      |

Colour comes from Tailwind text classes (`text-primary`, `text-muted-foreground`,
…) — the same accent orange as the rest of the app. Icons are `aria-hidden` by
default; pair with an accessible label on the interactive parent.

## Usage

```tsx
import { SkillIcon, SearchIcon } from "@/components/icons";

<SkillIcon className="text-primary" />        {/* 20px, brand orange */}
<SearchIcon size={16} className="text-muted-foreground" />
```

## Adding a glyph

1. Draw on the 24×24 grid, **stroke only** (no fills), rounded corners, ~2px
   visual weight. Match the existing glyphs' geometry.
2. Add a thin wrapper in [index.tsx](./index.tsx):
   ```tsx
   export function MyIcon(props: IconProps) {
     return <Icon {...props}><path d="…" /></Icon>;
   }
   ```
3. Keep paths minimal — these must read cleanly at 16px.

The current set: `SkillIcon`, `DocIcon`, `CompareIcon`, `SearchIcon`,
`VoiceIcon`, `SendIcon`, `SettingsIcon`, `SparkleIcon`.

The brand logo mark (the "ai" disc) lives separately at
`public/images/logo/aitana-logo.svg`.
