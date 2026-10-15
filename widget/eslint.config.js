// Task 5.1: flat ESLint config. Just @eslint/js's own recommended
// ruleset -- no third-party plugins -- split by environment (browser for
// the loader source, Node for build/test scripts) so neither set of
// globals wrongly flags the other as undefined.
import js from "@eslint/js";

export default [
  js.configs.recommended,
  {
    ignores: ["dist/**", "node_modules/**", "chat/dist/**", "playwright-report/**", "test-results/**"],
  },
  {
    files: ["loader/src/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        CSSStyleSheet: "readonly",
        URL: "readonly",
      },
    },
  },
  {
    files: ["scripts/**/*.mjs", "test/e2e/server.mjs", "playwright.config.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        process: "readonly",
        console: "readonly",
        URL: "readonly",
      },
    },
  },
  {
    files: ["loader/test/**/*.mjs", "test/e2e/**/*.spec.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        getComputedStyle: "readonly",
        console: "readonly",
      },
    },
  },
  {
    // test/e2e/public/ is served to the browser as a fixture, not
    // bundled/imported as a module -- plain browser globals, no import.
    files: ["test/e2e/public/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        window: "readonly",
      },
    },
  },
];
