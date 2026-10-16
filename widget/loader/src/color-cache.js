// Task [ops bugfix]: the launcher's tenant brand color (docs/SPEC.md
// §11 design direction: the platform computes the text color on the
// primary color, which always meets WCAG AA). Same reasoning as
// position-cache.js's own header: `widget_config.colors` only the CHAT
// bundle ever fetches (POST /session) -- the loader itself never calls
// the backend at all, by design (lazy, tiny, no network before the
// launcher click). That means the loader cannot know the tenant's real
// primary color on a visitor's very first-ever page view -- the
// launcher renders at the default (indigo) until the chat bundle has
// loaded once and learned the real value (chat/src/mount.js applies it
// live, to the shared host element, the moment that happens).
//
// From the SECOND page view onward, this is fixed: the chat bundle
// caches the confirmed, ALREADY-COMPUTED color pair here (chat/src/
// color-cache.js's own counterpart), and the loader reads that cached
// value BEFORE it ever creates the launcher, so a returning visitor's
// launcher appears in the correct brand color from first paint too.
//
// [SECURITY-adjacent] defense in depth: localStorage is same-origin,
// written only by this project's own code, but a value sitting in it
// could still have been edited directly (devtools, or a same-origin XSS
// elsewhere) -- this is read back only as a CSSOM `setProperty()` value
// (never parsed as CSS text, see loader/src/widget.css's own header),
// so it is not a markup-injection vector either way, but validation
// stays strict and narrow regardless: `hex` must be exactly
// "#" + 6 lowercase hex digits, and `text` must be the exact literal
// "#ffffff" or "#000000" -- the only two values color-cache.js's own
// writer (chat/src/color-cache.js, built on contrast.js's existing
// `pickReadableTextColor`) ever produces. Anything else -- a longer
// string, `rgb(...)`, `url(...)`, `var(...)`, missing fields, malformed
// JSON -- is ignored outright and the default CSS literal applies
// instead, exactly like a missing cache entry.
const _memoryFallback = new Map();

function _storageKey(siteKey) {
  return `sf-color:${siteKey}`;
}

const HEX_COLOR = /^#[0-9a-f]{6}$/;

export function getCachedColor(siteKey) {
  const key = _storageKey(siteKey);
  let raw;
  try {
    raw = window.localStorage.getItem(key);
  } catch {
    raw = _memoryFallback.get(key) ?? null;
  }
  if (!raw) {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) {
    return null;
  }
  const { hex, text } = parsed;
  if (typeof hex !== "string" || !HEX_COLOR.test(hex)) {
    return null;
  }
  if (text !== "#ffffff" && text !== "#000000") {
    return null;
  }
  return { hex, text };
}
