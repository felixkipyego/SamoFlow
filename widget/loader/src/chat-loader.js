// Task 5.1: lazily loads the chat UI bundle (owned by Step 5.2) on first
// launcher click. A plain <script src="..."> element -- never eval, never
// an inline script body -- so it works under a strict tenant CSP whose
// script-src allows the widget's own origin but not 'unsafe-inline'/'unsafe-eval'.
//
// Idempotent by construction: a script tag for a given chatUrl is only
// ever inserted once, even if the launcher is clicked repeatedly before
// the first load finishes.
export function ensureChatLoaded(chatUrl) {
  if (document.querySelector(`script[data-samoflow-chat-url="${chatUrl}"]`)) {
    return;
  }
  const script = document.createElement("script");
  script.src = chatUrl;
  script.async = true;
  script.dataset.samoflowChatUrl = chatUrl;
  document.head.appendChild(script);
}
