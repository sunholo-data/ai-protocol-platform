import type { Config } from "tailwindcss"
import tailwindcssAnimate from "tailwindcss-animate"

const config = {
  darkMode: ['class'],
  content: [
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: {
        '2xl': '1400px'
      }
    },
    extend: {
      // FORKS: don't edit fonts or radii here — set them in your theme file
      // (public/brand/<fork>/theme.css). If this merged with a conflict, take
      // upstream's version; UPGRADING.md (2026-10-01) has the steps.
      fontFamily: {
        // Euclid Circular B — the customer's brand face, declared as @font-face
        // in globals.css. Was 'Euclid Circular A': a different family, and one
        // nothing ever loaded, so this stack silently resolved to system-ui on
        // every render.
        sans: ['Euclid Circular B', 'system-ui', 'sans-serif'],
        serif: ['Crimson Pro', 'Georgia', 'serif'],
      },
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        // Floating surfaces (menus, popovers, hover cards) and cards. Without
        // these, `bg-popover` / `bg-card` compiled to NOTHING and menus were
        // transparent — the text underneath bled through (2026-10-02). They
        // fall back to the page colours so a fork theme that never sets them
        // still gets an opaque surface.
        popover: {
          DEFAULT: 'hsl(var(--popover, var(--background)))',
          foreground: 'hsl(var(--popover-foreground, var(--foreground)))',
        },
        card: {
          DEFAULT: 'hsl(var(--card, var(--background)))',
          foreground: 'hsl(var(--card-foreground, var(--foreground)))',
        },
      },
      borderRadius: {
        // One corner radius for every surface (the theme's --radius), so a
        // small radius like the default 3px never goes to zero or negative.
        '2xl': 'var(--radius)',
        xl: 'var(--radius)',
        lg: 'var(--radius)',
        md: 'var(--radius)',
        sm: 'max(0px, calc(var(--radius) - 1px))',
        DEFAULT: 'var(--radius)',
      },
    },
  },
  plugins: [tailwindcssAnimate],
} satisfies Config

export default config
