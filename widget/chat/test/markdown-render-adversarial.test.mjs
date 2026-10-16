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

// --- PART A [SECURITY]: verified_domains link allowlist (2026-10-16/17) ----
//
// docs/DECISIONS_REGISTER.md's "Widget track close-out" forward
// commitment: a link is allowed if it is https AND (its EXACT URL is in
// this turn's own citation set OR its HOST exactly matches one of the
// tenant's own verified_domains). The two rules are additive, proven
// separately below, then together. No citation URLs anywhere in this
// section (`new Set()`) so every passing case here is proven via the
// verified-domains path alone, never accidentally via the citation one.
const VERIFIED = new Set(["verified.example"]);
const NO_CITATIONS = new Set();

test("a verified-domain link with no citation at all still renders as a real, safe anchor", () => {
  const tree = renderText("See [our site](https://verified.example/pricing) for more.", NO_CITATIONS, VERIFIED);
  const anchors = collectAnchors(tree);
  assert.equal(anchors.length, 1);
  assert.equal(anchors[0].href, "https://verified.example/pricing");
  assert.equal(anchors[0].text, "our site");
});

test("a verified-domain link still carries rel=noopener noreferrer and target=_blank", () => {
  const tree = renderText("[click](https://verified.example/x)", NO_CITATIONS, VERIFIED);
  const anchor = collectAnchors(tree)[0];
  assert.equal(anchor !== undefined, true);
  assert.equal(anchor.target, "_blank");
  assert.ok(anchor.rel.includes("noopener"));
  assert.ok(anchor.rel.includes("noreferrer"));
});

test("the two allowlists are additive: a citation URL on an UNVERIFIED host still renders, and a verified-domain URL with no citation still renders", () => {
  const cited = new Set(["https://cited-but-unverified.example/page"]);
  const tree = renderText(
    "[cited](https://cited-but-unverified.example/page) and [verified](https://verified.example/x)",
    cited,
    VERIFIED,
  );
  assert.equal(collectAnchors(tree).length, 2);
});

test("path trick: evil.com/verified.example is NOT the verified host, never linked", () => {
  const tree = renderText("[click](https://evil.com/verified.example)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
  assert.ok(flattenText(tree).includes("click"));
});

test("subdomain trick: verified.example.evil.com is a DIFFERENT host, never linked", () => {
  const tree = renderText("[click](https://verified.example.evil.com/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("subdomain of the verified domain itself is NOT implicitly covered (exact match only, matching docs/SPEC.md's own ingestion rule: 'subdomains count only if the tenant verifies them')", () => {
  const tree = renderText("[click](https://evil.verified.example/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("userinfo trick: https://verified.example@evil.com resolves to evil.com, never linked", () => {
  const tree = renderText("[click](https://verified.example@evil.com/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("punycode/IDN look-alike: a visually-similar homograph never matches the real verified domain (compared in punycode/ASCII form)", () => {
  // A Cyrillic "е" (U+0435) substituted for the Latin "e" in "verified" --
  // looks identical in most UIs, encodes to a totally different
  // punycode string, so it can never collide with the real ASCII host.
  const lookalike = "verifiеd.example";
  const tree = renderText(`[click](https://${lookalike}/x)`, NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("a GENUINE IDN verified domain still matches once canonicalized to punycode on both sides", () => {
  const idnVerified = new Set(["xn--caf-dma.example"]); // café.example
  const tree = renderText("[click](https://café.example/x)", NO_CITATIONS, idnVerified);
  assert.equal(collectAnchors(tree).length, 1);
});

test("mixed case: VERIFIED.EXAMPLE still matches the lowercase stored domain", () => {
  const tree = renderText("[click](https://VERIFIED.EXAMPLE/x)", NO_CITATIONS, VERIFIED);
  assert.equal(collectAnchors(tree).length, 1);
});

test("trailing dot: verified.example. still matches", () => {
  const tree = renderText("[click](https://verified.example./x)", NO_CITATIONS, VERIFIED);
  assert.equal(collectAnchors(tree).length, 1);
});

test("an explicit port does not change the host match (verification is host-only, port-agnostic)", () => {
  const tree = renderText("[click](https://verified.example:8443/x)", NO_CITATIONS, VERIFIED);
  assert.equal(collectAnchors(tree).length, 1);
});

test("http (downgrade from https) on the verified domain is never linked", () => {
  const tree = renderText("[click](http://verified.example/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("a protocol-relative //verified.example link is never linked (no scheme at all)", () => {
  const tree = renderText("[click](//verified.example/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("javascript:/data: with a verified-looking path component never becomes a clickable anchor", () => {
  for (const url of [
    "javascript:alert(1)//verified.example",
    "data:text/html,verified.example",
  ]) {
    const tree = renderText(`[click](${url})`, NO_CITATIONS, VERIFIED);
    assert.deepEqual(collectAnchors(tree), [], `scheme should be rejected: ${url}`);
  }
});

test("backslash authority trick still resolves honestly and is judged on the REAL host it names", () => {
  // WHATWG treats \\ like / for http(s) -- this one genuinely resolves
  // to verified.example, so it SHOULD match (the parser is not being
  // fooled into naming the wrong host; it is reading the same host a
  // real browser's own navigation would).
  const tree = renderText("[click](https:\\\\verified.example\\x)", NO_CITATIONS, VERIFIED);
  assert.equal(collectAnchors(tree).length, 1);
});

test("encoded-slash userinfo trick still resolves to the real host, never verified.example", () => {
  const tree = renderText("[click](https://verified.example%2f@evil.com/x)", NO_CITATIONS, VERIFIED);
  assert.deepEqual(collectAnchors(tree), []);
});

test("an empty verified-domains set never matches anything, including a plausible exact host", () => {
  const tree = renderText("[click](https://verified.example/x)", NO_CITATIONS, new Set());
  assert.deepEqual(collectAnchors(tree), []);
});

test("a missing verified-domains argument (default) behaves identically to an empty set", () => {
  const tree = renderText("[click](https://verified.example/x)", NO_CITATIONS);
  assert.deepEqual(collectAnchors(tree), []);
});
