// Task 5.1: the headless-browser test required by the task -- loads a
// test page served with a real strict CSP response header (no
// 'unsafe-inline', no 'unsafe-eval') and checks the shadow root mounts,
// the host page's styles don't leak IN, the widget's styles don't leak
// OUT, lazy-loading only happens on click, and no CSP violation was ever
// reported (the live proof that nothing here needs eval or inline code).
import { test, expect } from "@playwright/test";

test("mounts an open shadow root under a strict CSP, isolates styles both ways, and lazy-loads the chat bundle only on click", async ({
  page,
}) => {
  const cspViolations = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      cspViolations.push(msg.text());
    }
  });

  await page.goto("/");

  const hasOpenShadowRoot = await page.evaluate(() => {
    const host = document.getElementById("samoflow-widget-host");
    return Boolean(host && host.shadowRoot && host.shadowRoot.querySelector("button"));
  });
  expect(hasOpenShadowRoot).toBe(true);

  expect(cspViolations, `unexpected CSP violations: ${cspViolations.join(" | ")}`).toEqual([]);

  // The host page's own red/Comic-Sans styles must not leak into the
  // shadow tree's launcher button.
  const launcherStyle = await page.evaluate(() => {
    const button = document
      .getElementById("samoflow-widget-host")
      .shadowRoot.querySelector("button");
    const computed = getComputedStyle(button);
    return { color: computed.color, fontFamily: computed.fontFamily };
  });
  expect(launcherStyle.color).not.toBe("rgb(255, 0, 0)");
  expect(launcherStyle.fontFamily).not.toContain("Comic Sans MS");

  // The widget's own styles must not leak out onto the host page.
  const hostMarkerFont = await page.evaluate(
    () => getComputedStyle(document.getElementById("host-page-marker")).fontFamily,
  );
  expect(hostMarkerFont).toContain("Comic Sans MS");

  // Lazy-load: not fetched before the first click.
  const loadedBeforeClick = await page.evaluate(
    () => window.__SAMOFLOW_CHAT_STUB_LOADED__,
  );
  expect(loadedBeforeClick).toBeUndefined();

  await page.evaluate(() => {
    document
      .getElementById("samoflow-widget-host")
      .shadowRoot.querySelector("button")
      .click();
  });
  await page.waitForFunction(() => window.__SAMOFLOW_CHAT_STUB_LOADED__ === true);

  // Clicking again must not insert a second <script> tag for the same URL.
  await page.evaluate(() => {
    document
      .getElementById("samoflow-widget-host")
      .shadowRoot.querySelector("button")
      .click();
  });
  const chatScriptCount = await page.evaluate(
    () => document.querySelectorAll('script[data-samoflow-chat-url]').length,
  );
  expect(chatScriptCount).toBe(1);

  // No global leaked beyond this test's own stub marker.
  const leakedGlobal = await page.evaluate(
    () => "SamoFlowWidget" in window || "samoflow" in window,
  );
  expect(leakedGlobal).toBe(false);
});
