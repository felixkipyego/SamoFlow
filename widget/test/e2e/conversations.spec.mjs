// Task 5.3: "Previous chats" end to end, under a real strict CSP --
// opening a past conversation, proving a link in its stored text
// renders as plain text (never live), and the retry button after a
// real failed/retried turn.
import { test, expect } from "@playwright/test";

async function openChat(page) {
  await page.goto("/chat-fixture.html");
  await page.locator("#samoflow-widget-host").locator("css=button.samoflow-launcher").click();
  return page.locator("#samoflow-widget-host").locator("css=.sf-panel");
}

test("opens a previous conversation and renders its stored link as plain text, never live", async ({ page }) => {
  const cspViolations = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      cspViolations.push(msg.text());
    }
  });

  const panel = await openChat(page);
  await panel.locator('css=[aria-label="Previous chats"]').click();

  const conversationItem = panel.locator("css=.sf-conversation-item");
  await expect(conversationItem).toHaveCount(1);
  await conversationItem.click();

  await expect(panel).toContainText("What are your hours?");
  // The renderer's own real behavior for a rejected link (Step 5.2):
  // the TEXT survives, the brackets/URL do not -- "everything else
  // renders as plain text" means the link's text content, never the
  // raw markdown source. The real, security-relevant assertion is the
  // one right after this: no live <a> exists for it anywhere.
  await expect(panel).toContainText("See our hours page for details.");

  const historyAnchors = await panel.locator("css=.sf-message-assistant a").count();
  expect(historyAnchors).toBe(0);

  expect(cspViolations).toEqual([]);
});

test("the empty-conversations state shows real copy, not a blank panel", async ({ page }) => {
  // Intercepts the real network request and returns a genuinely empty
  // list -- the fixture server's own default is always non-empty, so
  // this is the real way to exercise this state deterministically
  // (rather than guessing at timing), per Playwright's own
  // request-mocking support.
  await page.route("**/api/v1/conversations", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ conversations: [] }) }),
  );

  const panel = await openChat(page);
  await panel.locator('css=[aria-label="Previous chats"]').click();
  await expect(panel.locator("css=.sf-empty-state")).toBeVisible();
  await expect(panel).toContainText("haven't started a chat");
});

test("a conversations-list load failure shows a real error state with its own retry", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/v1/conversations", (route) => {
    requestCount += 1;
    if (requestCount === 1) {
      return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [{ conversation_id: "c1", title: "Old chat", created_at: "t", last_message_at: "t" }] }),
    });
  });

  const panel = await openChat(page);
  await panel.locator('css=[aria-label="Previous chats"]').click();
  await expect(panel.locator("css=.sf-error-state")).toBeVisible();

  await panel.locator("css=.sf-error-state .sf-retry").click();
  await expect(panel.locator("css=.sf-conversation-item")).toHaveCount(1);
});

test("a failed turn shows a retry button, and retrying with the same text succeeds", async ({ page }) => {
  const panel = await openChat(page);
  const input = panel.locator("css=.sf-input");
  await input.fill("fail once then succeed");
  await panel.locator("css=.sf-send").click();

  // First attempt fails (the fixture server's own stateful 503-once
  // behavior) -- the retry button must appear on the failed bubble.
  const retryButton = panel.locator("css=.sf-retry");
  await expect(retryButton).toBeVisible();
  await expect(panel).toContainText("temporarily unavailable");

  await retryButton.click();
  await expect(panel).toContainText("Here is a safe answer.");
});
