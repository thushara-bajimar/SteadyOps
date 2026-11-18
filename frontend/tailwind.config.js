/** Colors are CSS variables (see src/index.css) so light/dark are separately tuned. */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        page: v("page"), surface: v("surface"), sunken: v("sunken"), line: v("line"),
        ink: v("ink"), ink2: v("ink2"), muted: v("muted"), accent: v("accent"),
        good: v("good"), warning: v("warning"), serious: v("serious"), critical: v("critical"),
      },
      fontFamily: { sans: ["Inter", "system-ui", "Segoe UI", "sans-serif"], mono: ["JetBrains Mono", "Consolas", "monospace"] },
    },
  },
  plugins: [],
};
