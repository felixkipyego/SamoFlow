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
        // [SECURITY] Step 7-follow-up, Part C: appearance.js's own
        // bounded-timeout fetch -- the loader's first network call ever.
        fetch: "readonly",
        AbortController: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
      },
    },
  },
  {
    files: ["chat/src/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        URL: "readonly",
        fetch: "readonly",
        crypto: "readonly",
        TextDecoder: "readonly",
        // Part D: chat-controller.js's own stop()/AbortController wiring.
        AbortController: "readonly",
      },
    },
  },
  {
    files: ["chat/test/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        URL: "readonly",
        TextEncoder: "readonly",
        setTimeout: "readonly",
        // Part D: stop()'s own unit test fakes a real fetch/
        // ReadableStream abort-rejection, same idiom as loader/test/
        // appearance.test.mjs's own hangingFetch.
        DOMException: "readonly",
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
        // Part D: "trigger stoppable stream"'s own sleep() helper.
        setTimeout: "readonly",
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
        setTimeout: "readonly",
        fetch: "readonly",
        DOMException: "readonly",
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
