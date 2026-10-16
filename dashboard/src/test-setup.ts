import "@testing-library/jest-dom/vitest";

// jsdom does not implement matchMedia (Step 6.6: src/lib/theme.ts's own
// prefers-color-scheme check needs it) -- a minimal stub, not a real
// media-query engine: every query reports "not matching" by default,
// matching jsdom's own general stance of not implementing CSS layout.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}
