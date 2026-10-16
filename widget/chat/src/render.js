// Task 5.2 [SECURITY]: turns the restricted markdown AST (markdown.js)
// into Preact vnodes -- the SECOND, independent safety layer (never
// trusts the server's own 3.4.c escaping alone).
//
// The one structural property that makes "raw HTML disabled" and
// "no inline-script needs" true by construction, not by filtering:
// every leaf value handed to Preact below is a plain JS string passed
// as ordinary vnode children. `dangerouslySetInnerHTML` is never
// imported, never called, anywhere in this file or this component --
// grep for it as a standing invariant, not a one-time check. A plain
// string child is always rendered as a DOM TEXT NODE, which the
// browser never re-parses as markup, regardless of what characters it
// contains (`<script>`, a raw `<img onerror=...>`, anything) -- so
// there is no "tag-stripping" step anywhere here, because there is
// nothing for a stray `<`/`>` to ever open.
//
// Links are the ONE place this module sets a live, navigable
// attribute (`href`) from parsed content, so they are the one place
// gated: allowed only if `isHttpsUrl(url)` AND (`url` is EXACTLY one of
// `allowedUrls` -- this turn's own real citation URLs -- OR `url`'s own
// HOST exactly matches one of `verifiedDomains`, the tenant's own
// verified domains from `widget_config`, PART A of the [SECURITY]
// batch follow-up, 2026-10-16/17, docs/DECISIONS_REGISTER.md's
// "Widget track close-out" forward commitment). The two rules are
// ADDITIVE, never a loosening of each other: a citation URL is a
// specific page SamoFlow itself retrieved and cited for THIS exact
// answer (narrower and safer than any domain-level rule, and sidesteps
// IDN/homograph-domain detection entirely -- a homograph/typosquat URL
// simply isn't a byte-for-byte match for any real citation); a
// verified-domain match is wider (any https page on that exact host,
// not just a cited one) but still requires the SAME tenant to have
// proven they own that domain (docs/SPEC.md §5's own domain-
// verification flow) -- and EXACT host match only, never a subdomain:
// docs/SPEC.md §5's own ingestion rule ("Subdomains count only if the
// tenant verifies them") is applied identically here, so `evil.
// verified.example` is a DIFFERENT host from `verified.example` and
// never matches it, the same way it would never be crawled either.
// `url-safety.js`'s own `urlHostname()` uses the real WHATWG URL
// parser (never a hand-rolled host extractor) specifically so a
// homograph/IDN look-alike canonicalises to its OWN distinct punycode
// form rather than being compared by eye -- confirmed live, not
// assumed (see that module's own header). A link that fails BOTH
// rules renders its TEXT content only, as plain inline content --
// never the raw `[text](url)` source, never a dead `<a>` with no href
// -- exactly "everything else renders as plain text". Images are
// disabled unconditionally: only the alt text is ever shown, the URL
// is discarded, no `<img>` is ever created.
import { h, Fragment } from "preact";
import { isHttpsUrl, urlHostname } from "./url-safety.js";

const NO_VERIFIED_DOMAINS = new Set();

export function renderMarkdown(blocks, allowedUrls, verifiedDomains = NO_VERIFIED_DOMAINS) {
  return blocks.map((block, index) => _renderBlock(block, allowedUrls, verifiedDomains, index));
}

function _renderBlock(block, allowedUrls, verifiedDomains, key) {
  if (block.type === "codeblock") {
    return h("pre", { key, className: "sf-codeblock" }, h("code", null, block.value));
  }
  return h(
    "p",
    { key, className: "sf-paragraph" },
    renderInline(block.children, allowedUrls, verifiedDomains),
  );
}

export function renderInline(nodes, allowedUrls, verifiedDomains = NO_VERIFIED_DOMAINS) {
  return nodes.map((node, index) => _renderInlineNode(node, allowedUrls, verifiedDomains, index));
}

function _renderInlineNode(node, allowedUrls, verifiedDomains, key) {
  switch (node.type) {
    case "text":
      return node.value;
    case "code":
      return h("code", { key }, node.value);
    case "strong":
      return h("strong", { key }, renderInline(node.children, allowedUrls, verifiedDomains));
    case "em":
      return h("em", { key }, renderInline(node.children, allowedUrls, verifiedDomains));
    case "image_disabled":
      return node.alt;
    case "link": {
      const children = renderInline(node.children, allowedUrls, verifiedDomains);
      const httpsOk = isHttpsUrl(node.url);
      const citationMatch = httpsOk && allowedUrls.has(node.url);
      const domainMatch = httpsOk && verifiedDomains.has(urlHostname(node.url));
      if (citationMatch || domainMatch) {
        return h(
          "a",
          { key, href: node.url, target: "_blank", rel: "noopener noreferrer nofollow" },
          children,
        );
      }
      return h(Fragment, { key }, children);
    }
    default:
      return null;
  }
}
