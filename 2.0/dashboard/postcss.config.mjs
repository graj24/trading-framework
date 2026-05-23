// Tailwind v4 lives behind a single PostCSS plugin; no tailwind.config.ts content
// hooks are needed for source globs because v4 auto-detects source files.
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
