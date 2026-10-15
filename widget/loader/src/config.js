// Task 5.1: resolves the loader's configuration from its own <script> tag.
//
// Pure by design: takes a minimal {getAttribute, src} object and never
// touches `document`/`window` itself, so it is unit-testable in plain
// Node (loader/test/config.test.mjs) with no DOM library as a dependency.
// The real DOM wiring (document.currentScript, shadow root mounting) is
// covered instead by the Playwright test (test/e2e/widget.spec.mjs),
// which runs against a real browser DOM -- the one thing a Node unit test
// cannot exercise without adding a DOM-emulation dependency (rule 8: no
// dependency without a real need).
//
// data-base-url has no built-in fallback (decision #6, PROJECT_SPEC.md
// §5): the loader's own hostname is not assumed to be the API's hostname,
// so a missing data-base-url is treated as a configuration error, not
// defaulted to window.location or the script's own origin.
export function resolveConfig(scriptEl) {
  const siteKey = scriptEl.getAttribute("data-site-key");
  if (!siteKey) {
    return {
      error:
        "missing data-site-key attribute on the SamoFlow widget <script> tag",
    };
  }

  const baseUrl = scriptEl.getAttribute("data-base-url");
  if (!baseUrl) {
    return {
      error:
        "missing data-base-url attribute on the SamoFlow widget <script> tag",
    };
  }

  // The chat UI bundle and the launcher's stylesheet both default to
  // living next to the loader bundle (same directory, same origin) -- a
  // deployment convenience, not a hostname assumption about the API.
  // Both are always overridable.
  const chatUrl = resolveSiblingUrl(scriptEl, "chat.js", "data-chat-url");
  if (chatUrl.error) {
    return chatUrl;
  }

  const cssUrl = resolveSiblingUrl(scriptEl, "widget.css", "data-css-url");
  if (cssUrl.error) {
    return cssUrl;
  }

  return { siteKey, baseUrl, chatUrl: chatUrl.url, cssUrl: cssUrl.url };
}

// Shared by chatUrl/cssUrl above: an attribute override, or a URL
// resolved relative to this <script> tag's own src.
function resolveSiblingUrl(scriptEl, defaultFilename, overrideAttribute) {
  const override = scriptEl.getAttribute(overrideAttribute);
  if (override) {
    return { url: override };
  }

  if (!scriptEl.src) {
    return {
      error: `the SamoFlow widget <script> tag has no resolvable src URL, and no ${overrideAttribute} override was given`,
    };
  }
  try {
    return { url: new URL(`./${defaultFilename}`, scriptEl.src).toString() };
  } catch {
    return {
      error: `the SamoFlow widget <script> tag's src is not a resolvable URL: ${scriptEl.src}`,
    };
  }
}
