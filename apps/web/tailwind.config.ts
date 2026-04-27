import type { Config } from "tailwindcss";

/**
 * Tailwind v4 reads most config from the CSS layer (see globals.css `@theme`),
 * but a TS config is still supported for `content` globs and JS-side token
 * exposure. The colour palette below is PROVISIONAL — replace once the
 * Undivided Systems brand kit lands (CONTEXT.md §22).
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "#0B0F14",
        surface: "#11161D",
        "surface-elevated": "#161D26",
        border: "#1F2937",
        "text-primary": "#E5E7EB",
        "text-muted": "#9CA3AF",
        accent: "#3B82F6",
      },
    },
  },
  plugins: [],
};

export default config;
