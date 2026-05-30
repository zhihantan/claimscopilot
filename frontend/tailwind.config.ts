import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        // Surface
        canvas:   "#FAFAF9",
        surface:  "#FFFFFF",
        sunken:   "#F4F4F2",
        line:     "#E7E5E4",
        // Ink
        ink:      "#0E0E10",
        muted:    "#6B7280",
        soft:     "#9CA3AF",
        // Accent
        brand:    "#3B5BFF",
        brand2:   "#2A45D0",
        // Status
        ok:       "#0F8A52",
        warn:     "#B45309",
        danger:   "#C2410C",
        info:     "#1D6FE0",
      },
      boxShadow: {
        card:   "0 1px 2px rgba(16,24,40,0.04), 0 1px 1px rgba(16,24,40,0.02)",
        pop:    "0 8px 30px rgba(16,24,40,0.08)",
        focus:  "0 0 0 3px rgba(59,91,255,0.15)",
      },
      borderRadius: {
        lg: "10px",
        xl: "14px",
      },
      animation: {
        "pulse-fade": "pulse-fade 1.8s ease-in-out infinite",
        "shimmer":    "shimmer 1.4s linear infinite",
      },
      keyframes: {
        "pulse-fade": {
          "0%,100%": { opacity: "0.5" },
          "50%":     { opacity: "1" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-200px 0" },
          "100%": { backgroundPosition: "calc(200px + 100%) 0" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
