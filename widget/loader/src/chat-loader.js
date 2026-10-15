// Task 5.1: lazily loads the chat UI bundle (owned by Step 5.2) on first
// launcher click. A plain <script src="..."> element -- never eval, never
// an inline script body -- so it works under a strict tenant CSP whose
// script-src allows the widget's own origin but not 'unsafe-inline'/'unsafe-eval'.
//
// Idempotent by construction: a script tag for a given chatUrl is only
// ever inserted once, even if the launcher is clicked repeatedly before
// the first load finishes.
//
// Task 5.2: `handoff` ({shadowRoot, launcherEl, siteKey, baseUrl}) is
// the one, momentary, namespaced-global hand-off Step 5.1's own "leaks
// no globals (or exactly one namespaced one, justified)" rule
// anticipated -- the chat bundle needs a live reference to the loader's
// own shadow root and launcher button (not serializable as a data-*
// string attribute), and this is the one real need that justifies it.
// Set immediately before the script tag is inserted, read once by
// chat/src/index.js (synchronously, at its own top-level execution,
// same discipline as document.currentScript) and deleted there --
// never a long-lived global.
export function ensureChatLoaded(chatUrl, handoff) {
  if (document.querySelector(`script[data-samoflow-chat-url="${chatUrl}"]`)) {
    return;
  }
  window.__SAMOFLOW_WIDGET__ = handoff;
  const script = document.createElement("script");
  script.src = chatUrl;
  script.async = true;
  script.dataset.samoflowChatUrl = chatUrl;
  document.head.appendChild(script);
}
