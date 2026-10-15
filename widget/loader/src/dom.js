// Task 5.1: builds the launcher button inside the widget's shadow root.
// Styling is a real external stylesheet (<link rel="stylesheet">), not
// an inline <style> element: CSP's style-src applies inside shadow trees
// too (shadow DOM does not create a separate CSP context), and an
// external stylesheet from an allowed origin is the one styling
// mechanism that never needs 'unsafe-inline' -- the same reasoning
// script-src already requires for <script src>. See widget.css's own
// header for the live CSP proof (test/e2e/widget.spec.mjs).
//
// Task 5.5: fixes the flash-of-unstyled-content this step's own Part D
// report named as a known minor cosmetic gap. The button is created
// and appended only AFTER the stylesheet's own `load` (or `error`, so a
// network hiccup can never leave the launcher permanently missing)
// event fires -- there is never a moment where an unstyled
// `.samoflow-launcher` exists in the DOM at all, which is a stronger
// fix than trying to hide-then-reveal it (that would need its own CSS
// rule to apply correctly before the real stylesheet has loaded, the
// identical chicken-and-egg problem this sidesteps entirely).
export function createLauncher(shadowRoot, cssUrl) {
  return new Promise((resolve) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = cssUrl;

    const mountButton = () => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "samoflow-launcher";
      button.setAttribute("aria-label", "Open chat");
      button.textContent = "Chat";
      shadowRoot.appendChild(button);
      resolve(button);
    };
    link.addEventListener("load", mountButton, { once: true });
    link.addEventListener("error", mountButton, { once: true });

    shadowRoot.appendChild(link);
  });
}
