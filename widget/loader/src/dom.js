// Task 5.1: builds the launcher button inside the widget's shadow root.
// Styling is a real external stylesheet (<link rel="stylesheet">), not
// an inline <style> element: CSP's style-src applies inside shadow trees
// too (shadow DOM does not create a separate CSP context), and an
// external stylesheet from an allowed origin is the one styling
// mechanism that never needs 'unsafe-inline' -- the same reasoning
// script-src already requires for <script src>. See widget.css's own
// header for the live CSP proof (test/e2e/widget.spec.mjs).
export function createLauncher(shadowRoot, cssUrl) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = cssUrl;
  shadowRoot.appendChild(link);

  const button = document.createElement("button");
  button.type = "button";
  button.className = "samoflow-launcher";
  button.setAttribute("aria-label", "Open chat");
  button.textContent = "Chat";

  shadowRoot.appendChild(button);
  return button;
}
