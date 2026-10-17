// Task 5.1: the widget loader (docs/SPEC.md §11). Bundled by esbuild into
// a single classic (non-module) IIFE script, dist/widget.js -- see
// scripts/build.mjs. Must stay a classic script: document.currentScript
// is always null for <script type="module">, and this is the only
// reliable way to find the tag that loaded this file and read its
// data-* attributes.
//
// Leaks no globals of its OWN: everything below runs inside esbuild's
// IIFE wrapper. Task 5.2 extends this with exactly one, momentary,
// namespaced global (`window.__SAMOFLOW_WIDGET__`, set by
// chat-loader.js's own `ensureChatLoaded()` immediately before the chat
// script tag is inserted, deleted by chat/src/index.js the moment it
// reads it) -- the one real need (handing the chat bundle a live
// shadowRoot/launcher reference, not serializable as a string
// attribute) that justifies the escape hatch this file's own original
// comment already anticipated. No OTHER public JS API is exposed.
import { resolveConfig } from "./config.js";
import { createLauncher } from "./dom.js";
import { ensureChatLoaded } from "./chat-loader.js";
import { fetchAppearance } from "./appearance.js";

// Captured synchronously, at top-level script execution -- NOT inside
// any deferred callback. document.currentScript is only set while this
// script's own top-level code is running; by the time a later event
// callback (e.g. DOMContentLoaded) fires, it has already reverted to null.
const loaderScriptEl = document.currentScript;

async function mount() {
  if (!loaderScriptEl) {
    console.error(
      "[SamoFlow] widget.js could not find its own <script> tag " +
        "(document.currentScript was null during initial execution) -- " +
        "include it as a plain, non-module <script> tag.",
    );
    return;
  }

  const config = resolveConfig(loaderScriptEl);
  if (config.error) {
    console.error(`[SamoFlow] ${config.error}`);
    return;
  }

  const host = document.createElement("div");
  host.id = "samoflow-widget-host";

  // Open, not closed (deliberate choice -- see docs/progress/widget.md):
  // nothing secret ever lives in this shadow tree (the session token is
  // held in memory in JS, never in the DOM), so closed mode would buy no
  // real security property here, only extra friction for debugging and
  // for the tests that must assert the shadow root mounted.
  const shadowRoot = host.attachShadow({ mode: "open" });
  document.body.appendChild(host);

  createLauncher(shadowRoot, config.cssUrl).then((launcher) => {
    let chatLoadTriggered = false;
    launcher.addEventListener("click", () => {
      if (chatLoadTriggered) {
        return;
      }
      chatLoadTriggered = true;
      ensureChatLoaded(config.chatUrl, {
        shadowRoot,
        hostEl: host,
        launcherEl: launcher,
        siteKey: config.siteKey,
        baseUrl: config.baseUrl,
      });
    });
  });

  // [SECURITY] Step 7-follow-up, Part C, fixed 2026-10-17: the launcher's
  // own real brand color/position, on EVERY page view -- including a
  // visitor's very first-ever one, not only a returning visitor (the gap
  // the retired color-cache.js/position-cache.js localStorage mechanism
  // could only ever close for a returning visitor). Runs CONCURRENTLY
  // with host/shadowRoot/launcher creation above, never blocking it --
  // the original shape awaited this BEFORE creating the host at all,
  // which meant the launcher did not exist in the DOM until this fetch
  // resolved on every single page load, silently contradicting this
  // same code's own stated goal ("a slow/unreachable API cannot
  // meaningfully delay first paint", appearance.js) -- the launcher's
  // first paint was delayed by the FULL round trip, every time, not
  // only when the API was slow or down. Found via a genuine CI-only
  // Playwright flake (test/e2e/accessibility.spec.mjs's keyboard-nav
  // test), reproduced locally only inside a real ubuntu-24.04 container
  // under load, never on a fast, unloaded native Chromium. On any
  // failure, `appearance` is still null and both widget.css's own
  // hardcoded defaults (color) and the plain `sf-position-left` class
  // omission (position, defaulting right) apply unchanged, exactly like
  // a cache miss used to -- now simply the launcher's OWN first frame
  // too, rather than something that only ever showed up on a slow
  // network.
  const appearance = await fetchAppearance(config.baseUrl, config.siteKey);
  if (appearance) {
    host.style.setProperty("--sf-primary", appearance.primary);
    host.style.setProperty("--sf-primary-text", appearance.primaryText);
    if (appearance.position === "left") {
      host.classList.add("sf-position-left");
    }
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
