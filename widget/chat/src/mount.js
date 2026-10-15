// Task 5.2: mounts the Preact chat app into the SAME shadow root the
// loader (Step 5.1) already created -- the loader and the launcher
// button are one shared widget host, per docs/SPEC.md §11's "a single
// host element".
import { h, render } from "preact";
import { App } from "./App.js";
import { ChatController } from "./chat-controller.js";
import { validateWidgetConfig } from "./config-validation.js";

const DEFAULT_CONFIG = validateWidgetConfig({});

export async function mountChatApp({ shadowRoot, launcherEl, siteKey, baseUrl, cssUrl }) {
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

  render(h(App, { controller, config, launcherEl }), panelHost);
}
