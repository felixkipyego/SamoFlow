// Task 5.1: the widget loader (docs/SPEC.md §11). Bundled by esbuild into
// a single classic (non-module) IIFE script, dist/widget.js -- see
// scripts/build.mjs. Must stay a classic script: document.currentScript
// is always null for <script type="module">, and this is the only
// reliable way to find the tag that loaded this file and read its
// data-* attributes.
//
// Leaks no globals: everything below runs inside esbuild's IIFE wrapper,
// and nothing is assigned onto `window`. No public JS API is exposed
// because nothing in docs/SPEC.md §11 asks for host-page programmatic
// control in v1; if that need shows up later, it becomes one namespaced
// global added deliberately, not a default.
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
    ensureChatLoaded(config.chatUrl);
  });

  document.body.appendChild(host);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
