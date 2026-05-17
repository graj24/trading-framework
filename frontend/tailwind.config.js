/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Semantic layers - base palette
        surface: {
          950: "#030712", // Deepest background
          900: "#0a0e17", // Main background (was bg-primary)
          800: "#111827", // Card/Panel background (was bg-secondary)
          750: "#162032", // Elevated surfaces
          700: "#1f2937", // Borders & dividers (was bg-tertiary)
          600: "#374151",
        },
        // Semantic layers - text
        content: {
          primary: "#f9fafb",    // High emphasis
          secondary: "#9ca3af",  // Medium emphasis
          muted: "#6b7280",      // Low emphasis (was text-muted)
          disabled: "#4b5563",   // Disabled state
        },
        // Accent colors - semantic
        accent: {
          primary: "#3b82f6",    // Brand blue
          success: "#10b981",    // Green - gains
          danger: "#ef4444",     // Red - losses
          warning: "#f59e0b",    // Amber - caution
          info: "#06b6d4",       // Cyan - info
          purple: "#8b5cf6",    // Purple - special
        },
        // Signal colors for trading
        signal: {
          bullish: "#10b981",
          bearish: "#ef4444",
          neutral: "#6b7280",
          strong: "#22c55e",
          weak: "#eab308",
        },
        // Border tokens
        border: {
          DEFAULT: "#1f2937",
          hover: "#374151",
          active: "#3b82f6",
        },
        // Chart colors
        chart: {
          1: "#3b82f6",
          2: "#10b981",
          3: "#f59e0b",
          4: "#ef4444",
          5: "#8b5cf6",
          6: "#06b6d4",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ["Inter Display", "Inter", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', '"Fira Code"', "monospace"],
        numeric: ["JetBrains Mono", "monospace"],
      },
      // Spacing for consistent rhythm
      spacing: {
        18: "4.5rem",
        22: "5.5rem",
      },
      // Border radius scale
      borderRadius: {
        none: "0",
        sm: "0.125rem",
        DEFAULT: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      // Shadow system
      shadow: {
        glow: "0 0 20px rgba(59, 130, 246, 0.15)",
        "glow-success": "0 0 20px rgba(16, 185, 129, 0.15)",
        "glow-danger": "0 0 20px rgba(239, 68, 68, 0.15)",
        card: "0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -2px rgba(0, 0, 0, 0.2)",
        elevated: "0 10px 15px -3px rgba(0, 0, 0, 0.4), 0 4px 6px -4px rgba(0, 0, 0, 0.3)",
      },
      // Animation tokens
      animation: {
        "fade-in": "fadeIn 0.2s ease-out",
        "slide-up": "slideUp 0.3s ease-out",
        "slide-down": "slideDown 0.3s ease-out",
        "scale-in": "scaleIn 0.2s ease-out",
        "pulse-glow": "pulseGlow 2s ease-in-out infinite",
        "number-tick": "numberTick 0.3s ease-out",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideDown: {
          "0%": { opacity: "0", transform: "translateY(-10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        scaleIn: {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        pulseGlow: {
          "0%, 100%": { boxShadow: "0 0 5px rgba(59, 130, 246, 0.2)" },
          "50%": { boxShadow: "0 0 20px rgba(59, 130, 246, 0.4)" },
        },
        numberTick: {
          "0%": { transform: "translateY(-100%)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      // Backdrop blur
      backdropBlur: {
        xs: "2px",
      },
    },
  },
  plugins: [],
};
