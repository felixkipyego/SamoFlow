// Task 5.2: the headless-browser proof that the real chat UI -- not
// just the loader -- works end to end under a real strict CSP (no
// 'unsafe-inline', no 'unsafe-eval'): session creation, a streamed
// answer, a real citation link, and zero CSP violations throughout.
import { test, expect } from "@playwright/test";

test("sends a message, streams a real answer with a safe citation link, under a strict CSP with zero violations", async ({
  page,
}) => {
  const cspViolations = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      cspViolations.push(msg.text());
    }
  });

  await page.goto("/chat-fixture.html");

  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Hi! How can we help?");

  const input = panel.locator("css=.sf-input");
  await input.fill("What are your hours?");
  await panel.locator("css=.sf-send").click();

  await expect(panel).toContainText("Here is a safe answer.");

  const citationLink = panel.locator("css=.sf-citations a");
  await expect(citationLink).toHaveText("Example source");
  await expect(citationLink).toHaveAttribute("href", "https://example.com/source-page");
  await expect(citationLink).toHaveAttribute("target", "_blank");
  await expect(citationLink).toHaveAttribute("rel", "noopener noreferrer nofollow");

  expect(cspViolations, `unexpected CSP violations: ${cspViolations.join(" | ")}`).toEqual([]);
});

// [ops bugfix] regression test: the launcher used to stay the loader
// CSS's hardcoded default (#4f46e5) forever, even once the real tenant
// color (the fixture's own #14b8a6, deliberately NOT the default -- see
// server.mjs) had been fetched and was already correctly applied to the
// header/bubbles/Send. This asserts BOTH the header and the launcher
// -- a sibling of the panel under the shared shadow root, not a
// descendant of it -- end up on the exact same real, non-default color.
//
// [SECURITY] Step 7-follow-up, Part C: the launcher now gets this same
// color from GET /api/v1/widget-appearance BEFORE any click at all (a
// separate, dedicated test below proves the brand-new-visitor case in
// full) -- `toHaveCSS` auto-retries, so this correctly waits out that
// async fetch rather than racing it with a one-shot read.
test("every brand-colored element, including the launcher, picks up the real tenant color", async ({ page }) => {
  await page.goto("/chat-fixture.html");

  const launcher = page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher");
  await expect(launcher).toHaveCSS("background-color", "rgb(20, 184, 166)");

  await launcher.click();

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  const header = panel.locator("css=.sf-header");
  await expect(header).toHaveCSS("background-color", "rgb(20, 184, 166)");

  // Still the SAME real tenant color, now via the real session's own
  // AUTHORITATIVE config (mount.js) rather than the appearance
  // endpoint's own pre-click best-effort guess -- the two mechanisms
  // must agree.
  await expect(launcher).toHaveCSS("background-color", "rgb(20, 184, 166)");

  const sendButton = panel.locator("css=.sf-send");
  await expect(sendButton).toHaveCSS("background-color", "rgb(20, 184, 166)");
});

// [SECURITY] Step 7-follow-up, Part C: the real fix for "the launcher
// shows the wrong color/position until the visitor's first click" --
// confirmed on a GENUINELY brand-new visitor (Playwright's own default
// per-test isolated browser context: no cookies, no localStorage, no
// prior visit of any kind), with NO click at all. server.mjs's own
// fake GET /api/v1/widget-appearance returns `{primary: "#14b8a6",
// position: "left"}` -- deliberately both non-default values, so a
// regression to either the loader CSS's own default color OR the
// default right-hand position would fail this test loudly.
test("a brand-new visitor sees the tenant color and position on the launcher with no click", async ({
  page,
}) => {
  await page.goto("/chat-fixture.html");

  const launcher = page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher");
  await expect(launcher).toHaveCSS("background-color", "rgb(20, 184, 166)");

  const host = page.locator("#samoflow-widget-host");
  await expect(host).toHaveClass(/sf-position-left/);
  await expect(host).toHaveCSS("left", "20px");
});

// [ops bugfix] the real reported bug, reproduced and fixed: after the
// first answer finished, the input/Send stayed disabled forever --
// `controller.isStreaming` (a plain mutable property, never Preact
// state) was read directly for the `disabled` prop, so Preact had no
// way to know when it flipped back to false (see App.js's own header
// comment for the exact mechanism). This sends a SECOND real message
// after the first completes -- the literal reproduction.
test("the input re-enables after a turn completes, and a second message can genuinely be sent", async ({ page }) => {
  await page.goto("/chat-fixture.html");
  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");

  await input.fill("first question");
  await send.click();
  await expect(panel).toContainText("Here is a safe answer.");

  // The textarea itself is the real proof (Send is ALSO correctly
  // disabled right now for an unrelated, intentional reason: the input
  // is empty after the first send cleared it -- not the bug this test
  // guards against).
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();

  await input.fill("second question");
  await expect(send).toBeEnabled();
  await send.click();
  await expect(panel.locator("css=.sf-message")).toHaveCount(4); // visitor+assistant x2
  await expect(input).toBeEnabled();
});

// [ops bugfix] docs/SPEC.md §10's own real contract addition (Step
// 6.2): `done` now carries `conversation_id` on every turn -- this is
// what let chat-controller.js remove the old GET /conversations
// workaround entirely (that workaround had ALSO silently stopped
// working in any real cross-origin deployment -- see the register). A
// genuine 3-turn conversation where turn 2 depends on turn 1's own
// content is the one proof that the SAME conversation_id really is
// threaded through every turn, not just that no error occurred.
test("a 3-turn conversation: turn 2 answers from what turn 1 actually said, proving the same conversation_id carries forward", async ({
  page,
}) => {
  await page.goto("/chat-fixture.html");
  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");

  await input.fill("my favorite color is teal");
  await send.click();
  await expect(panel).toContainText("Here is a safe answer.");
  await expect(input).toBeEnabled();

  await input.fill("what did I just tell you?");
  await send.click();
  await expect(panel).toContainText('You told me: "my favorite color is teal"');
  await expect(input).toBeEnabled();

  await input.fill("what did I just tell you?");
  await send.click();
  await expect(panel).toContainText('You told me: "what did I just tell you?"');
  await expect(input).toBeEnabled();
});

// [ops bugfix] every named way a turn can end must re-enable AND
// refocus the input -- no terminal state exists in this widget's own
// design that should leave it dead (docs/SPEC.md §9/§11: quota-
// exhausted still routes through the SAME `done` event with a fixed
// fallback reply; no other state is named as permanently disabling
// chat). Covers 409, 503, a real SSE `error` event (model/timeout), and
// a dropped connection (stream ends with no `done` at all).
for (const [name, mockRoute] of [
  [
    "a 409 (conversation busy)",
    (route) => route.fulfill({ status: 409, contentType: "application/json", body: '{"detail":"this conversation is busy, please retry"}' }),
  ],
  [
    "a 503 (service unavailable)",
    (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"service temporarily unavailable, please retry"}' }),
  ],
  [
    "a real SSE timeout error event",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: error\ndata: {"code":"timeout","message":"ignored","v":1}\n\n',
      }),
  ],
  [
    "a dropped connection (stream ends with no done event)",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: token\ndata: {"text":"partial","v":1}\n\n',
      }),
  ],
]) {
  test(`the input re-enables and refocuses after ${name}`, async ({ page }) => {
    await page.route("**/api/v1/chat", mockRoute);
    await page.goto("/chat-fixture.html");
    await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
    const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
    const input = panel.locator("css=.sf-input");
    const send = panel.locator("css=.sf-send");

    await input.fill("hello");
    await send.click();

    await expect(input).toBeEnabled();
    await expect(input).toBeFocused();
    await expect(send).toBeDisabled(); // empty text again -- the OTHER disabling reason, not isStreaming
  });
}

// [SECURITY] Step 7-follow-up, Part B: the block above uses
// `page.route().fulfill()`, which a direct adversarial probe confirmed
// bypasses the browser's own CORS enforcement entirely (a fulfilled
// response with NO Access-Control-Allow-Origin header still succeeded)
// -- so it proves the WIDGET's own re-enable/refocus behavior, but
// nothing about CORS. These three go through the real, separate-origin
// fake API server instead (test/e2e/server.mjs's own named triggers),
// so the browser's real preflight/ACAO check genuinely applies -- each
// one was confirmed to go red when its own response's
// Access-Control-Allow-Origin header was temporarily removed (see
// PROJECT_SPEC.md's own decision-log entry for this task for the full
// adversarial-removal transcript).
for (const [name, message, expectedText] of [
  ["a 409 (conversation busy)", "trigger 409 busy", "wait a moment"],
  ["a 429 (rate limited)", "trigger 429 rate limited", "too quickly"],
  ["quota exhausted (a normal 200 stream with a fixed reply)", "trigger quota exhausted", "follow up by email"],
]) {
  test(`${name} shows the calm message, via the real cross-origin API`, async ({ page }) => {
    await page.goto("/chat-fixture.html");
    await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
    const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
    const input = panel.locator("css=.sf-input");
    const send = panel.locator("css=.sf-send");

    await input.fill(message);
    await send.click();

    await expect(panel).toContainText(expectedText, { ignoreCase: true });
    await expect(input).toBeEnabled();
    await expect(input).toBeFocused();
  });
}

// [ops bugfix]: closing and reopening the panel WHILE a turn is still
// in flight must not leave the input stuck once it reopens -- the turn
// keeps running in the background (the panel only toggles `hidden`; the
// Preact tree, and the controller driving it, are never unmounted), so
// a hidden-at-the-time .focus() call is a no-op by spec; this proves
// the SECOND chance (the same effect re-running on `open` too) picks it
// up correctly once the panel is visible again.
test("closing and reopening the panel mid-stream still re-enables and refocuses the input once it finishes", async ({
  page,
}) => {
  let resolveRoute;
  await page.route("**/api/v1/chat", (route) => {
    resolveRoute = () =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: done\ndata: {"message_id":"m1","usage":null,"offer_escalation":false,"conversation_id":"c1","v":1}\n\n',
      });
  });

  await page.goto("/chat-fixture.html");
  // A Locator, not a one-shot evaluateHandle() -- it re-resolves the
  // query on every action, so it is safe to click it again after the
  // panel re-renders (an ElementHandle would still reference the OLD
  // node even if Preact had replaced it, which it does not here, but a
  // Locator is the correct, idiomatic tool for "the same logical
  // element, clicked more than once" regardless).
  const launcher = page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher");
  await launcher.click();

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");
  await input.fill("hello");
  await send.click();
  await expect(input).toBeDisabled();

  // Close the panel while the turn is still in flight (the route above
  // has not been asked to resolve yet).
  await launcher.click();
  await expect(panel).toBeHidden();

  // Now let the turn actually finish while the panel is closed.
  resolveRoute();
  await page.waitForTimeout(200);

  // Reopen -- the input must be enabled and focused immediately, not
  // require a further click or reload.
  await launcher.click();
  await expect(panel).toBeVisible();
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();
});

test("the launcher toggles the panel open and closed after the chat bundle has loaded", async ({ page }) => {
  await page.goto("/chat-fixture.html");
  const launcher = page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher");

  await launcher.click();
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  await launcher.click();
  await expect(panel).toBeHidden();

  await launcher.click();
  await expect(panel).toBeVisible();
});

// Part D (docs/SPEC.md §9's "Stop mid-stream"): the real, explicit stop
// signal, proven end to end against server.mjs's own "trigger
// stoppable stream" fixture (a genuinely slow, multi-token stream with
// real delays, so there is a real window to click Stop while it is
// still running -- not a race against an instant fake response).
test("the visitor can stop a streaming answer: the Stop button cancels generation, the input re-enables and refocuses immediately, and a new message sends right away", async ({
  page,
}) => {
  await page.goto("/chat-fixture.html");
  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  const input = panel.locator("css=.sf-input");
  // Part D: the SAME element swaps between Send and Stop on isStreaming
  // (App.js) -- one locator for both, exactly like the real button.
  const sendOrStop = panel.locator("css=.sf-send");
  const liveRegion = panel.locator('css=[role="status"][aria-live="polite"]');

  await input.fill("trigger stoppable stream");
  await sendOrStop.click();

  // Accessible label AND visible text both flip the moment streaming
  // starts -- a screen reader hears "Stop generating", not a stale
  // "Send" left over from before the click.
  await expect(sendOrStop).toHaveText("Stop");
  await expect(sendOrStop).toHaveAttribute("aria-label", "Stop generating");
  await expect(input).toBeDisabled();
  // At least one real token has genuinely streamed -- this is a stop
  // mid-generation, not a click that raced the turn's own instant end.
  await expect(panel.locator("css=.sf-message-assistant").last()).toContainText("slow token 0");

  await sendOrStop.click(); // Stop

  // The input re-enables and refocuses, and the button reverts to
  // Send, WITHOUT waiting for the slow stream's own ~3s natural
  // completion -- the real proof that this is a local abort, not a
  // wait for the server's own round trip.
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();
  await expect(sendOrStop).toHaveText("Send");
  await expect(sendOrStop).toHaveAttribute("aria-label", "Send");

  // A visible, distinct "(stopped)" marker -- never silently shown as
  // a normal completed answer.
  await expect(panel.locator("css=.sf-message-assistant").last()).toContainText("(stopped)");
  // The SAME accessible state change reaches the hidden live region --
  // screen readers get the state, not just the visible marker.
  await expect(liveRegion).toContainText("(stopped)");

  // The real proof behind "stop then an immediate new message
  // succeeds": no 409, no leftover disabled state, no delay.
  await input.fill("a normal follow-up message");
  await sendOrStop.click();
  await expect(panel.locator("css=.sf-message-visitor").last()).toContainText(
    "a normal follow-up message",
  );
});
