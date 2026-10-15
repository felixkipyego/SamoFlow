// Task 5.2 [SECURITY]: the adversarial pass required before this step
// can be reported done, each case a committed regression test, asserted
// directly against the Preact VNODE TREE (never a rendered HTML string)
// -- see vnode-helpers.mjs's own header for why that is possible and
// sufficient without jsdom/a browser.
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderText, collectAnchors, anyDangerousProp, flattenText } from "./vnode-helpers.mjs";

const CITED = new Set(["https://good.example.com/page"]);

test("ordinary answer text with a real cited link renders a real, safe anchor", () => {
  const tree = renderText("See [the docs](https://good.example.com/page) for more.", CITED);
  const anchors = collectAnchors(tree);
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].href, "https://good.example.com/page");
  assert.equal(anchors[0].text, "the docs");
});

test("XSS through answer text: a raw <script> tag never becomes a dangerous node, only literal text", () => {
  const tree = renderText('<script>alert(1)</script> and "onload" tricks', CITED);
  assert.equal(anyDangerousProp(tree), false);
  assert.ok(flattenText(tree).includes("<script>alert(1)</script>"));
});

test("javascript:, data:, vbscript: link destinations never become a clickable anchor", () => {
  for (const scheme of ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)"]) {
    const tree = renderText(`[click me](${scheme})`, CITED);
    assert.deepEqual(collectAnchors(tree), [], `scheme should be rejected: ${scheme}`);
    assert.ok(flattenText(tree).includes("click me"));
  }
});

test("mixed-case and whitespace-padded schemes never become a clickable anchor", () => {
  for (const url of ["JAVASCRIPT:alert(1)", "jav\tascript:alert(1)", "java\nscript:alert(1)"]) {
    const tree = renderText(`[click me](${url})`, CITED);
    assert.deepEqual(collectAnchors(tree), []);
  }
});

test("a protocol-relative //evil link never becomes a clickable anchor", () => {
  const tree = renderText("[click me](//evil.example.com/path)", CITED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.ok(flattenText(tree).includes("click me"));
});

test("reference-style links are not implemented: never become a clickable anchor", () => {
  const tree = renderText(
    "See [the docs][1] for more.\n\n[1]: https://good.example.com/page",
    CITED,
  );
  assert.deepEqual(collectAnchors(tree), []);
  // Falls through to literal text containing the brackets.
  assert.ok(flattenText(tree).includes("[the docs][1]"));
});

test("nested brackets in link text are matched correctly (depth-tracked, not naive)", () => {
  const tree = renderText("[outer [inner] text](https://good.example.com/page)", CITED);
  const anchors = collectAnchors(tree);
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].text, "outer [inner] text");
});

test("nested brackets whose URL is NOT cited still never produce an anchor", () => {
  const tree = renderText("[outer [inner] text](https://not-cited.example.com)", CITED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.ok(flattenText(tree).includes("outer [inner] text"));
});

test("HTML-entity-encoded javascript: scheme in a link destination is decoded THEN rejected", () => {
  const tree = renderText("[click me](&#106;avascript:alert(1))", CITED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("HTML entities in ordinary text decode to a literal character, never re-parsed as new syntax", () => {
  const tree = renderText("5 &lt; 10 and 10 &gt; 5, a &amp; b", CITED);
  assert.equal(flattenText(tree), "5 < 10 and 10 > 5, a & b");
  // Decoding "&#91;" must NOT manufacture a live "[" that starts a new
  // link construct -- confirmed directly: decoding happens only at
  // the leaf, after structural scanning is already finished.
  const tricky = renderText("&#91;click me&#93;&#40;https://good.example.com/page&#41;", CITED);
  assert.deepEqual(collectAnchors(tricky), []);
  assert.equal(flattenText(tricky), "[click me](https://good.example.com/page)");
});

test("the fullwidth look-alike construct never becomes an image/link, with or without a real citation", () => {
  const fullwidth = "！［click］（https://good.example.com/page）"; // ！［click］（...）
  const tree = renderText(fullwidth, CITED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.equal(anyDangerousProp(tree), false);
  // Inert Unicode text, rendered completely unchanged (no NFKC fold
  // happened anywhere in the pipeline).
  assert.equal(flattenText(tree), fullwidth);
});

test("an IDN/homograph-shaped domain is never linked unless it is an EXACT citation match", () => {
  const notCited = renderText("[click me](https://xn--80ak6aa92e.com/)", CITED);
  assert.deepEqual(collectAnchors(notCited), []);

  const citedPunycode = new Set(["https://xn--80ak6aa92e.com/"]);
  const cited = renderText("[click me](https://xn--80ak6aa92e.com/)", citedPunycode);
  assert.equal(collectAnchors(cited).length, 1);
});

test("a bare autolink <https://evil> is never recognised as a link or raw HTML", () => {
  const tree = renderText("go to <https://evil.example.com> now", CITED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.equal(anyDangerousProp(tree), false);
  assert.ok(flattenText(tree).includes("<https://evil.example.com>"));
});

test("images are unconditionally disabled: only the alt text ever renders, never an <img> or the URL", () => {
  const tree = renderText("![a cat picture](https://good.example.com/cat.png)", CITED);
  assert.equal(anyDangerousProp(tree), false);
  assert.equal(flattenText(tree), "a cat picture");
  assert.ok(!flattenText(tree).includes("cat.png"));
});

test("a code span's content is never scanned for links, even if it looks like one", () => {
  const tree = renderText("`[click me](javascript:alert(1))`", CITED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.equal(flattenText(tree), "[click me](javascript:alert(1))");
});

test("a fenced code block's content is never scanned for links either", () => {
  const tree = renderText("```\n[click me](javascript:alert(1))\n```", CITED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("strong/em formatting still recurses safely through an embedded malicious link attempt", () => {
  const tree = renderText("**[click me](javascript:alert(1))**", CITED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("a citation URL that is http (not https) is never linked even if present in the allow-set", () => {
  const httpCited = new Set(["http://good.example.com/page"]);
  const tree = renderText("[click me](http://good.example.com/page)", httpCited);
  assert.deepEqual(collectAnchors(tree), []);
});

test("an unterminated code span falls through to plain text, not a hang or a swallowed answer", () => {
  const tree = renderText("some `unterminated code and [a link](javascript:alert(1))", CITED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("empty input and whitespace-only input render without throwing (bad input: empty)", () => {
  assert.doesNotThrow(() => renderText(""));
  assert.doesNotThrow(() => renderText("   \n\n   "));
});
