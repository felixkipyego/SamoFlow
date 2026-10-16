// Task 5.2: mounts the Preact chat app into the SAME shadow root the
// loader (Step 5.1) already created -- the loader and the launcher
// button are one shared widget host, per docs/SPEC.md §11's "a single
// host element".
import { h, render } from "preact";
import { App } from "./App.js";
import { ChatController } from "./chat-controller.js";
import { validateWidgetConfig } from "./config-validation.js";
import { setCachedPosition } from "./position-cache.js";
import { setCachedColor } from "./color-cache.js";
import { pickReadableTextColor, toHexColor } from "./contrast.js";

const DEFAULT_CONFIG = validateWidgetConfig({});

export async function mountChatApp({ shadowRoot, hostEl, launcherEl, siteKey, baseUrl, cssUrl }) {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = cssUrl;
  shadowRoot.appendChild(link);

  const panelHost = document.createElement("div");
  shadowRoot.appendChild(panelHost);

  const controller = new ChatController(baseUrl, siteKey);
  let config = DEFAULT_CONFIG;
  try {
    const rawConfig = await controller.ensureSession();
    config = validateWidgetConfig(rawConfig);
  } catch {
    // Calm fallback: mount with safe defaults regardless -- the first
    // real send() attempt retries ensureSession() itself and surfaces
    // a proper fixed error state then (never a blank/broken panel,
    // per docs/SPEC.md §11's own "never a blank panel" state list).
  }

  // Task 5.5: apply the REAL position now (the loader may have started
  // the launcher on the cached/default side) and cache it for the
  // loader's own next page load -- see loader/src/position-cache.js's
  // header for the full round trip. A plain class toggle on the host
  // element the loader itself created (never an inline style mutation).
  if (hostEl) {
    hostEl.classList.toggle("sf-position-left", config.position === "left");
  }
  setCachedPosition(siteKey, config.position);

  // [ops bugfix]: tenant branding, applied via CSSOM property assignment
  // on `hostEl` -- the shadow HOST, not the chat panel -- because the
  // launcher button (loader/src/dom.js) and the chat panel are SIBLINGS
  // under the one shared shadow root this component mounts into; a
  // custom property set on either sibling would never reach the other
  // (CSS custom properties inherit to DESCENDANTS only). `hostEl` is the
  // one ancestor both already share, so setting it here is what makes
  // every brand-colored element -- including the launcher, not just the
  // header/bubbles/Send this used to cover when it lived in App.js's own
  // effect on the panel alone -- pick up the real tenant color, through
  // the exact same CSP-safe mechanism (see chat.css's own header for the
  // live CSP proof). Cached (color-cache.js) so a RETURNING visitor's
  // launcher starts in the correct color from first paint too -- see
  // loader/src/color-cache.js's own header for that round trip.
  if (hostEl) {
    const textColor = pickReadableTextColor(config.colors.primary);
    hostEl.style.setProperty("--sf-primary", config.colors.primary);
    hostEl.style.setProperty("--sf-primary-text", textColor);
    const hex = toHexColor(config.colors.primary);
    if (hex) {
      setCachedColor(siteKey, hex, textColor);
    }
  }

  render(h(App, { controller, config, launcherEl }), panelHost);
}
