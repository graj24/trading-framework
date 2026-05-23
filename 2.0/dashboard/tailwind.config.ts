// Tailwind v4 — most config moved to CSS via @theme. This file exists so
// shadcn/ui's `components.json` has something to point at, and so anyone
// reaching for a JS/TS-side override has an obvious home.
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
};

export default config;
