/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        serif: ["Newsreader", "Georgia", "serif"],
      },
      colors: {
        bg: {
          canvas: "var(--bg-canvas)",
          surface: "var(--bg-surface)",
          "surface-sunken": "var(--bg-surface-sunken)",
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
        brand: "var(--brand)",
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
        statement: ["28px", { lineHeight: "36px", fontWeight: "500" }],
        title: ["22px", { lineHeight: "28px", fontWeight: "600" }],
        label: ["12px", { lineHeight: "16px", letterSpacing: "0.06em", fontWeight: "600" }],
        metric: ["34px", { lineHeight: "40px", fontWeight: "600" }],
        body: ["15px", { lineHeight: "22px" }],
        small: ["12.5px", { lineHeight: "18px" }],
      },
      boxShadow: {
        elevated: "0 2px 8px rgba(0,0,0,0.06)",
      },
      transitionDuration: {
        120: "120ms",
        150: "150ms",
        180: "180ms",
      },
    },
  },
  plugins: [],
};
