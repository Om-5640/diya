/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
      colors: {
        bg: {
          base: "var(--bg-base)",
          panel: "var(--bg-panel)",
          "panel-raised": "var(--bg-panel-raised)",
        },
        border: {
          subtle: "var(--border-subtle)",
          strong: "var(--border-strong)",
        },
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
        },
        accent: {
          solar: "var(--accent-solar)",
          wind: "var(--accent-wind)",
          battery: "var(--accent-battery)",
          diesel: "var(--accent-diesel)",
        },
        status: {
          good: "var(--status-good)",
          warn: "var(--status-warn)",
          critical: "var(--status-critical)",
        },
      },
      fontSize: {
        display: ["32px", { lineHeight: "38px" }],
        h1: ["20px", { lineHeight: "28px" }],
        h2: ["13px", { lineHeight: "18px", letterSpacing: "0.04em" }],
        body: ["14px", { lineHeight: "20px" }],
        meta: ["12px", { lineHeight: "16px" }],
      },
      transitionDuration: {
        140: "140ms",
        150: "150ms",
      },
    },
  },
  plugins: [],
};
