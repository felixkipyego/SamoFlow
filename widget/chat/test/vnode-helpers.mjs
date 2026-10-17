// Task 5.2: test-only helpers for inspecting a Preact vnode tree
// directly -- `h()` returns plain JS objects with no DOM involved, so
// these adversarial tests can assert on the TREE STRUCTURE itself
// (e.g. "no vnode anywhere has type 'a' with an unsafe href") without
// needing jsdom or a real browser at all.
import { renderMarkdown } from "../src/render.js";
import { parseMarkdown } from "../src/markdown.js";

export function renderText(text, allowedUrls = new Set(), verifiedDomains = new Set()) {
  return renderMarkdown(parseMarkdown(text), allowedUrls, verifiedDomains);
}

function _walk(node, visit) {
  if (node === null || node === undefined || typeof node !== "object") {
    return;
  }
  if (Array.isArray(node)) {
    for (const child of node) {
      _walk(child, visit);
    }
    return;
  }
  if ("type" in node) {
    visit(node);
    _walk(node.props?.children, visit);
  }
}

export function collectAnchors(tree) {
  const anchors = [];
  _walk(tree, (node) => {
    if (node.type === "a") {
      anchors.push({
        href: node.props.href,
        text: _flattenText(node.props.children),
        rel: node.props.rel,
        target: node.props.target,
      });
    }
  });
  return anchors;
}

export function anyDangerousProp(tree) {
  let found = false;
  _walk(tree, (node) => {
    const props = node.props ?? {};
    if ("dangerouslySetInnerHTML" in props) {
      found = true;
    }
    if (node.type === "img" || node.type === "script" || node.type === "iframe" || node.type === "svg") {
      found = true;
    }
  });
  return found;
}

export function flattenText(tree) {
  return _flattenText(tree);
}

function _flattenText(node) {
  if (node === null || node === undefined || typeof node === "boolean") {
    return "";
  }
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(_flattenText).join("");
  }
  if (typeof node === "object" && "props" in node) {
    return _flattenText(node.props?.children);
  }
  return "";
}
