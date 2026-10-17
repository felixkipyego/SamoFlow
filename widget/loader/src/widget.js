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

// Out-of-band maintenance batch (2026-10-17), PART E: the launcher must
// show ONCE, already in its final look -- not the default color/position
// painted first and then switched once GET /api/v1/widget-appearance
// resolves (a color flash on every visitor; a jump across the screen for
// a left-positioned tenant). 800ms, not the appearance fetch's own more
// patient 2000ms abort budget: the appearance endpoint is tiny, same-
// infra, cacheable (max-age=120) and already proven in production at a
// sub-second p95 (D-7follow-widget-appearance-endpoint) -- 800ms is
// generous slack above that real p95 while still keeping the worst case
// (a genuinely unreachable API) well under a 1-second-feels-instant
// rule of thumb for a visitor's very first paint of anything on the
// page. Below the task's own ~800ms ceiling, not at it, so there is
// headroom without needing to re-justify the number later.
const APPEARANCE_REVEAL_TIMEOUT_MS = 800;

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

  // Out-of-band maintenance batch (2026-10-17), PART E: held hidden
  // (widget.css's own `:host(.sf-pending) { visibility: hidden }`) until
  // `reveal()` below removes this class -- `visibility: hidden` (not
  // `display: none`) is what makes the launcher neither focusable nor
  // announced to assistive tech while pending (a hidden-but-laid-out
  // element is pulled out of both the tab order and the accessibility
  // tree by the UA itself, no extra `tabindex`/`aria-hidden` bookkeeping
  // needed), while keeping `all: initial`'s own box model untouched for
  // the moment it IS revealed.
  host.classList.add("sf-pending");

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

  // [SECURITY] Step 7-follow-up, Part C (2026-10-17), REVISED at the
  // out-of-band maintenance batch PART E (2026-10-17): the launcher's
  // own real brand color/position, on EVERY page view -- including a
  // visitor's very first-ever one, not only a returning visitor. The CI
  // fix that made host/shadowRoot/launcher creation synchronous (this
  // same file, `D-ci-fix-appearance-fetch-blocks-mount`) was correct on
  // its own terms, but left a NEW, real defect: the launcher now always
  // painted the DEFAULT color/position first and only switched once this
  // fetch resolved -- a color flash for every visitor, and a jump across
  // the screen for a left-positioned tenant. Fixed by revealing exactly
  // ONCE, with whichever of (a) a resolved `appearance` or (b) the
  // `APPEARANCE_REVEAL_TIMEOUT_MS` timeout happens first -- never a
  // paint-then-switch. `reveal()` sets every CSSOM property (the two
  // custom properties, the position class) and removes `sf-pending` in
  // the SAME synchronous call, so the browser's next paint is the first
  // and only one, already in its final look.
  let revealed = false;
  function reveal(appearance) {
    if (revealed) {
      // A late response arriving after the timeout already fired --
      // ignored outright, not merely "the DOM happens not to change
      // further": nothing below this guard ever runs for it.
      return;
    }
    revealed = true;
    if (appearance) {
      host.style.setProperty("--sf-primary", appearance.primary);
      host.style.setProperty("--sf-primary-text", appearance.primaryText);
      if (appearance.position === "left") {
        host.classList.add("sf-position-left");
      }
    }
    // No reveal transition: an instant, one-shot visibility toggle has
    // nothing for prefers-reduced-motion to disable in the first place
    // -- the simplest design that satisfies it, not an animation this
    // task would then have to gate behind a media-query check.
    host.classList.remove("sf-pending");
  }

  fetchAppearance(config.baseUrl, config.siteKey, {
    timeoutMs: APPEARANCE_REVEAL_TIMEOUT_MS,
  }).then(reveal);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount, { once: true });
} else {
  mount();
}
