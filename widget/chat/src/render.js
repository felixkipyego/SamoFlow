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
// gated: allowed only if `isHttpsUrl(url)` AND `url` is EXACTLY one of
// `allowedUrls` (this turn's own real citation URLs, see url-safety.js
// and the Step 5.1 Part C contract check for why an exact-match
// allowlist is used instead of a verified-domain check: a citation URL
// is a specific page SamoFlow itself retrieved and cited for this
// exact answer, which is both narrower and safer than any domain-level
// rule, and sidesteps IDN/homograph-domain detection entirely -- a
// homograph/typosquat URL simply isn't a byte-for-byte match for any
// real citation, the same as any other non-matching URL). A link that
// fails the gate renders its TEXT content only, as plain inline
// content -- never the raw `[text](url)` source, never a dead `<a>`
// with no href -- exactly "everything else renders as plain text".
// Images are disabled unconditionally: only the alt text is ever shown,
// the URL is discarded, no `<img>` is ever created.
import { h, Fragment } from "preact";
import { isHttpsUrl } from "./url-safety.js";

export function renderMarkdown(blocks, allowedUrls) {
  return blocks.map((block, index) => _renderBlock(block, allowedUrls, index));
}

function _renderBlock(block, allowedUrls, key) {
  if (block.type === "codeblock") {
    return h("pre", { key, className: "sf-codeblock" }, h("code", null, block.value));
  }
  return h("p", { key, className: "sf-paragraph" }, renderInline(block.children, allowedUrls));
}

export function renderInline(nodes, allowedUrls) {
  return nodes.map((node, index) => _renderInlineNode(node, allowedUrls, index));
}

function _renderInlineNode(node, allowedUrls, key) {
  switch (node.type) {
    case "text":
      return node.value;
    case "code":
      return h("code", { key }, node.value);
    case "strong":
      return h("strong", { key }, renderInline(node.children, allowedUrls));
    case "em":
      return h("em", { key }, renderInline(node.children, allowedUrls));
    case "image_disabled":
      return node.alt;
    case "link": {
      const children = renderInline(node.children, allowedUrls);
      if (isHttpsUrl(node.url) && allowedUrls.has(node.url)) {
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
