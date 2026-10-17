// Task 5.4 [SECURITY-adjacent]: the escalation panel end to end, under
// a real strict CSP -- the three required adversarial checks (no
// submission without consent; injection through contact fields stays
// inert; a double-submit is prevented), plus the real happy path and
// the real server-enforced 409 (one-open-per-conversation).
import { test, expect } from "@playwright/test";

async function openChatAndCompleteATurn(page) {
  await page.goto("/chat-fixture.html");
  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await panel.locator("css=.sf-input").fill("hi");
  await panel.locator("css=.sf-send").click();
  await expect(panel).toContainText("Here is a safe answer.");
  return panel;
}

test("the 'Talk to a person' button appears only after offer_escalation, and opens the consent panel", async ({ page }) => {
  const panel = await openChatAndCompleteATurn(page);
  const talkButton = panel.locator("css=.sf-talk-to-human");
  await expect(talkButton).toBeVisible();
  await talkButton.click();
  await expect(panel.locator("css=.sf-escalation-consent")).toContainText("share your conversation");
});

test("no submission without consent: the Send button is disabled, and no request is ever made, until the consent checkbox is checked", async ({
  page,
}) => {
  const panel = await openChatAndCompleteATurn(page);
  await panel.locator("css=.sf-talk-to-human").click();

  let escalateCalls = 0;
  await page.route("**/api/v1/conversations/**/escalate", (route) => {
    escalateCalls += 1;
    return route.continue();
  });

  const submitButton = panel.locator("css=.sf-escalation-submit");
  await expect(submitButton).toBeDisabled();
  // A disabled button cannot be meaningfully clicked via the real UI,
  // but force-dispatching the click proves the HANDLER itself also
  // refuses, not merely the disabled attribute (defense in depth,
  // matching this whole project's "never trust one layer alone" posture).
  await submitButton.click({ force: true });
  expect(escalateCalls).toBe(0);

  await panel.locator('css=input[type="checkbox"]').check();
  await expect(submitButton).toBeEnabled();
});

test("injection through contact fields (CRLF, a fake header line) stays inert -- rejected client-side, never sent", async ({ page }) => {
  const panel = await openChatAndCompleteATurn(page);
  await panel.locator("css=.sf-talk-to-human").click();
  await panel.locator('css=input[type="checkbox"]').check();

  // A real `<input type="text">`'s own value-sanitization algorithm
  // strips line breaks unconditionally (typed OR set via `.value`),
  // so a literal CRLF never even reaches this component's own JS --
  // confirmed live, not assumed. That is reassuring, not a gap: CRLF
  // injection through this field is already structurally impossible
  // one layer below this component's own code. This test instead uses
  // a different C0 control character (unit separator, \x1F) that is
  // NOT part of that line-break-specific sanitization, so it genuinely
  // reaches `validateContactFields()` and exercises the SAME
  // `CONTROL_CHARS` rejection path CR/LF itself would hit server-side.
  await panel.locator("#sf-esc-name").fill("Evil\x1fBcc: attacker@evil.example");

  let escalateCalls = 0;
  await page.route("**/api/v1/conversations/**/escalate", (route) => {
    escalateCalls += 1;
    return route.continue();
  });

  await panel.locator("css=.sf-escalation-submit").click();
  expect(escalateCalls).toBe(0);
  await expect(panel.locator("css=.sf-field-error")).toBeVisible();
});

test("a double-submit is prevented: two rapid clicks only ever send one real request", async ({ page }) => {
  const panel = await openChatAndCompleteATurn(page);
  await panel.locator("css=.sf-talk-to-human").click();
  await panel.locator('css=input[type="checkbox"]').check();

  let escalateCalls = 0;
  await page.route("**/api/v1/conversations/**/escalate", async (route) => {
    escalateCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 200)); // widen the race window
    return route.continue();
  });

  const submitButton = panel.locator("css=.sf-escalation-submit");
  await submitButton.click();
  await submitButton.click({ force: true }); // the button disables itself immediately; force to really try

  await expect(panel).toContainText("Your request has been sent", { timeout: 5000 });
  expect(escalateCalls).toBe(1);
});

test("the full happy path succeeds, and a second attempt on the same conversation gets the real server 409", async ({ page }) => {
  let captured = null;
  await page.route("**/api/v1/conversations/**/escalate", async (route) => {
    captured = { url: route.request().url(), headers: route.request().headers() };
    await route.continue();
  });

  const panel = await openChatAndCompleteATurn(page);
  await panel.locator("css=.sf-talk-to-human").click();
  await panel.locator('css=input[type="checkbox"]').check();
  await panel.locator("css=.sf-escalation-submit").click();
  await expect(panel).toContainText("Your request has been sent");
  expect(captured).not.toBeNull();

  // Escalation UI is one-shot per turn's own offer by design (the
  // button disappears once submitted) -- replays the EXACT real
  // request the widget itself just sent (same URL, same real
  // Authorization header, captured above), to prove the SERVER's own
  // one-open-per-conversation enforcement is real and independent of
  // the UI's own double-submit guard, not merely assumed.
  const secondAttemptStatus = await page.evaluate(
    async ({ url, authorization }) => {
      const response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json", authorization },
        body: JSON.stringify({}),
      });
      return response.status;
    },
    { url: captured.url, authorization: captured.headers.authorization },
  );
  expect(secondAttemptStatus).toBe(409);
});
