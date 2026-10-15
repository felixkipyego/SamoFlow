// Task 5.2: a deliberately small HTML entity decoder.
//
// Scope, stated plainly: numeric character references (`&#NNN;`,
// `&#xHHH;`) plus five named entities (`amp`, `lt`, `gt`, `quot`,
// `apos`) -- not CommonMark's full ~2000-entry named-entity table. A
// chat answer realistically needs `&amp;`/`&lt;`/`&gt;` (common in
// ordinary prose) and the numeric form (the one a scheme-obfuscation
// attempt like `&#106;avascript:` actually uses) far more than e.g.
// `&hearts;`; the full table is real bytes this component's 60KB
// budget does not need to spend. An unrecognized entity is left
// exactly as written (never guessed, never dropped).
//
// CALL ORDER IS THE SECURITY PROPERTY HERE, not just a style choice:
// this must only ever run on an already-structurally-parsed LEAF
// string (final text content, or an already-extracted link
// destination) -- never on raw text before markdown.js's own bracket/
// backtick/emphasis scanning. Decoding first would let an entity like
// `&#91;` manufacture a literal `[` that could then be misread as new
// markdown structure; decoding only the leaf value, as a terminal step
// whose output is never re-scanned, closes that off by construction.
const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

const ENTITY_PATTERN = /&(#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);/g;

function codePointToChar(codePoint) {
  if (!Number.isInteger(codePoint) || codePoint <= 0 || codePoint > 0x10ffff) {
    return "�";
  }
  if (codePoint >= 0xd800 && codePoint <= 0xdfff) {
    return "�"; // a lone surrogate value is not a valid code point
  }
  try {
    return String.fromCodePoint(codePoint);
  } catch {
    return "�";
  }
}

export function decodeEntities(value) {
  return value.replace(ENTITY_PATTERN, (whole, body) => {
    if (body[0] === "#") {
      const isHex = body[1] === "x" || body[1] === "X";
      const digits = isHex ? body.slice(2) : body.slice(1);
      const codePoint = Number.parseInt(digits, isHex ? 16 : 10);
      return codePointToChar(codePoint);
    }
    return Object.prototype.hasOwnProperty.call(NAMED, body) ? NAMED[body] : whole;
  });
}
