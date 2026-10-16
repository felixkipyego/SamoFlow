// Step 6.6: docs/SPEC.md §12's own "light and dark themes from the
// first screen" -- the CSS token system already supports both (src/
// index.css's own `.dark` class rules, every screen built on tokens
// only, no hard-coded colors), but nothing in the app ever applied
// that class until now. No new dependency (next-themes was tried and
// reverted earlier in this track for being unpinned, see git history)
// -- a `.dark` class toggle plus one localStorage key is the whole
// mechanism this needs.
const STORAGE_KEY = "samoflow-dashboard-theme";

export type Theme = "light" | "dark";

function prefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function getInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return prefersDark() ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}
