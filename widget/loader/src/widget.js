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

// Captured synchronously, at top-level script execution -- NOT inside
// any deferred callback. document.currentScript is only set while this
// script's own top-level code is running; by the time a later event
// callback (e.g. DOMContentLoaded) fires, it has already reverted to null.
const loaderScriptEl = document.currentScript;

function mount() {
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
  const launcher = createLauncher(shadowRoot, config.cssUrl);

  let chatLoadTriggered = false;
  launcher.addEventListener("click", () => {
    if (chatLoadTriggered) {
      return;
    }
    chatLoadTriggered = true;
    ensureChatLoaded(config.chatUrl, {
      shadowRoot,
      launcherEl: launcher,
      siteKey: config.siteKey,
      baseUrl: config.baseUrl,
    });
  });

  document.body.appendChild(host);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
