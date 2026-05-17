/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: "#0a0e17",
          secondary: "#111827",
          tertiary: "#1f2937",
        },
        text: {
          primary: "#f9fafb",
          secondary: "#9ca3af",
          muted: "#4b5563",
        },
        green: "#00d4aa",
        red: "#ff4d4d",
        gold: "#f59e0b",
        blue: "#3b82f6",
        purple: "#8b5cf6",
        orange: "#f97316",
        border: "#1f2937",
        "border-active": "#3b82f6",
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
