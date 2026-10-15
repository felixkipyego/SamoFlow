// Task 5.2: the chat UI's entry point -- lazy-loaded by the Step 5.1
// loader on first launcher click. Stays a classic (non-module) script
// for the identical reason widget.js does: `document.currentScript` is
// always null for `<script type="module">`, and this is how the
// script finds its own <script> tag (set up by loader/src/chat-
// loader.js's own `ensureChatLoaded()`).
import { mountChatApp } from "./mount.js";

// Captured synchronously, at top-level script execution -- see
// loader/src/widget.js's own header for why this cannot be deferred
// into any later callback.
const chatScriptEl = document.currentScript;

// The one-time, momentary handoff channel from the loader (the one
// namespaced global Step 5.1's own "leaks no globals (or exactly one
// namespaced one, justified)" rule anticipated): {shadowRoot,
// launcherEl, siteKey, baseUrl}. Read once, deleted immediately --
// never a long-lived global.
const handoff = window.__SAMOFLOW_WIDGET__;
delete window.__SAMOFLOW_WIDGET__;

if (handoff && chatScriptEl && chatScriptEl.src) {
  const cssUrl = new URL("./chat.css", chatScriptEl.src).toString();
  mountChatApp({ ...handoff, cssUrl });
}
