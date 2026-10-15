// Task 5.2 [SECURITY]: a small, deliberately RESTRICTED markdown parser
// -- not a general CommonMark implementation, and not a third-party
// markdown library (rule 8: a general-purpose parser would need heavy
// defanging -- html off, linkify off, autolink off, custom link/image
// renderers -- and would still need auditing for its own entity/
// encoding edge cases; a smaller, purpose-built surface, fully covered
// by this step's own adversarial test suite, is the lower-risk and
// lower-byte-count choice here).
//
// Supports exactly: inline code spans, fenced code blocks, **strong**/
// __strong__, *em*/_em_, and `[text](url)` links -- nothing else is
// recognised as syntax at all. In particular, this file implements NO
// autolink (`<https://...>`), NO raw inline HTML, NO reference-style
// links (`[text][ref]`), and NO bare-URL "linkify". Anything that
// isn't one of the five supported constructs falls through as plain
// text, character by character -- ambiguous or malformed syntax
// degrades to literal text, it never guesses.
//
// NO Unicode normalisation is ever called anywhere in this file. This
// is load-bearing, not an oversight: Unicode NFKC normalisation folds
// fullwidth look-alike punctuation (U+FF01 "！", U+FF3B "［", U+FF3D
// "］", U+FF08 "（", U+FF09 "）" -- the "Halfwidth and Fullwidth Forms"
// block) onto their plain ASCII compatibility equivalents (!, [, ], (,
// )). If this parser normalised before scanning, a string containing
// only those fullwidth characters -- inert, ordinary Unicode text to
// every ASCII-exact comparison below -- would be silently rewritten
// into live `![alt](url)` syntax that never existed in the original
// text. Structural scanning below is exact-codepoint comparison only,
// so a fullwidth look-alike is never recognised as syntax, with or
// without this comment; the comment states the invariant so a future
// edit does not "helpfully" add a `.normalize()` call upstream of it.
import { decodeEntities } from "./entities.js";

const ESCAPABLE = new Set([..."!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"]);

export function parseMarkdown(rawText) {
  const lines = String(rawText).split(/\r\n|\r|\n/);
  const blocks = [];
  let paragraphLines = [];

  const flushParagraph = () => {
    if (paragraphLines.length > 0) {
      blocks.push({ type: "paragraph", children: parseInline(paragraphLines.join("\n")) });
      paragraphLines = [];
    }
  };

  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (/^`{3,}$/.test(trimmed)) {
      const closeIndex = _findFenceClose(lines, i + 1);
      if (closeIndex !== -1) {
        flushParagraph();
        blocks.push({ type: "codeblock", value: lines.slice(i + 1, closeIndex).join("\n") });
        i = closeIndex + 1;
        continue;
      }
      // No closing fence found anywhere -- not treated as a fence at
      // all (never silently swallows the rest of the message as one
      // giant "code block"); falls through to ordinary paragraph text.
    }
    if (trimmed === "") {
      flushParagraph();
    } else {
      paragraphLines.push(lines[i]);
    }
    i += 1;
  }
  flushParagraph();
  return blocks;
}

function _findFenceClose(lines, from) {
  for (let i = from; i < lines.length; i += 1) {
    if (/^`{3,}$/.test(lines[i].trim())) {
      return i;
    }
  }
  return -1;
}

export function parseInline(text) {
  const nodes = [];
  let run = "";
  const chars = Array.from(text);
  let i = 0;

  const pushText = (value) => {
    if (value !== "") {
      nodes.push({ type: "text", value: decodeEntities(value) });
    }
  };
  const flushRun = () => {
    pushText(run);
    run = "";
  };

  while (i < chars.length) {
    const ch = chars[i];

    if (ch === "\\" && i + 1 < chars.length && ESCAPABLE.has(chars[i + 1])) {
      run += chars[i + 1];
      i += 2;
      continue;
    }

    if (ch === "`") {
      const tickRun = _countRun(chars, i, "`");
      const close = _findMatchingTickRun(chars, i + tickRun, tickRun);
      if (close !== -1) {
        flushRun();
        nodes.push({ type: "code", value: chars.slice(i + tickRun, close).join("") });
        i = close + tickRun;
        continue;
      }
      run += ch;
      i += 1;
      continue;
    }

    if ((ch === "*" || ch === "_") && chars[i + 1] === ch) {
      const close = _findClosingPair(chars, i + 2, ch + ch);
      if (close !== -1) {
        flushRun();
        nodes.push({ type: "strong", children: parseInline(chars.slice(i + 2, close).join("")) });
        i = close + 2;
        continue;
      }
    }
    if (ch === "*" || ch === "_") {
      const close = _findClosingSingle(chars, i + 1, ch);
      if (close !== -1 && close > i + 1) {
        flushRun();
        nodes.push({ type: "em", children: parseInline(chars.slice(i + 1, close).join("")) });
        i = close + 1;
        continue;
      }
    }

    if (ch === "!" || ch === "[") {
      const isImage = ch === "!" && chars[i + 1] === "[";
      const bracketStart = isImage ? i + 1 : i;
      if (chars[bracketStart] === "[") {
        const parsed = _tryParseLink(chars, bracketStart);
        if (parsed) {
          flushRun();
          if (isImage) {
            nodes.push({ type: "image_disabled", alt: decodeEntities(parsed.textRaw) });
          } else {
            nodes.push({
              type: "link",
              url: decodeEntities(parsed.urlRaw),
              children: parseInline(parsed.textRaw),
            });
          }
          i = parsed.next;
          continue;
        }
      }
    }

    run += ch;
    i += 1;
  }
  flushRun();
  return nodes;
}

function _countRun(chars, from, ch) {
  let n = 0;
  while (chars[from + n] === ch) {
    n += 1;
  }
  return n;
}

function _findMatchingTickRun(chars, from, length) {
  let i = from;
  while (i < chars.length) {
    if (chars[i] === "`") {
      const n = _countRun(chars, i, "`");
      if (n === length) {
        return i;
      }
      i += n;
    } else {
      i += 1;
    }
  }
  return -1;
}

function _findClosingPair(chars, from, marker) {
  for (let i = from; i < chars.length - 1; i += 1) {
    if (chars[i] === marker[0] && chars[i + 1] === marker[1]) {
      return i;
    }
  }
  return -1;
}

function _findClosingSingle(chars, from, marker) {
  for (let i = from; i < chars.length; i += 1) {
    if (chars[i] === marker) {
      return i;
    }
  }
  return -1;
}

// Attempts to parse `[text](url)` (or `[text](<url>)`) starting at the
// `[` found at `chars[openIndex]`. Returns null (never a link) if the
// brackets/parens are unbalanced or `]` is not immediately followed by
// `(` -- in which case the caller falls through to plain-text handling,
// never guessing a partial/ambiguous match into a link.
function _tryParseLink(chars, openIndex) {
  let depth = 1;
  let i = openIndex + 1;
  while (i < chars.length && depth > 0) {
    if (chars[i] === "[") depth += 1;
    else if (chars[i] === "]") depth -= 1;
    if (depth === 0) break;
    i += 1;
  }
  if (depth !== 0) {
    return null; // no matching "]" found at all
  }
  const textRaw = chars.slice(openIndex + 1, i).join("");
  const afterBracket = i + 1;
  if (chars[afterBracket] !== "(") {
    return null; // not immediately followed by "(" -- e.g. [text][ref], [text] (url)
  }

  let destStart = afterBracket + 1;
  let destEnd;
  if (chars[destStart] === "<") {
    const closeAngle = chars.indexOf(">", destStart + 1);
    if (closeAngle === -1 || chars[closeAngle + 1] !== ")") {
      return null;
    }
    const urlRaw = chars.slice(destStart + 1, closeAngle).join("");
    return { textRaw, urlRaw, next: closeAngle + 2 };
  }

  let parenDepth = 1;
  let j = destStart;
  while (j < chars.length && parenDepth > 0) {
    if (chars[j] === "\\" && j + 1 < chars.length) {
      j += 2;
      continue;
    }
    if (chars[j] === "(") parenDepth += 1;
    else if (chars[j] === ")") parenDepth -= 1;
    if (parenDepth === 0) break;
    j += 1;
  }
  if (parenDepth !== 0) {
    return null;
  }
  destEnd = j;
  const urlRaw = chars.slice(destStart, destEnd).join("");
  if (/\s/.test(urlRaw) && !urlRaw.startsWith("<")) {
    // A bare (non-angle-bracket) destination containing whitespace is
    // not a valid CommonMark destination -- reject rather than guess
    // which part was meant to be the URL.
    return null;
  }
  return { textRaw, urlRaw, next: destEnd + 1 };
}
