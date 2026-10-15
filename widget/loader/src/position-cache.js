// Task 5.5: the launcher's position (left/right, docs/SPEC.md §11) is
// part of `widget_config` (Step 5.2), which only the CHAT bundle ever
// fetches (POST /session) -- the loader itself never calls the backend
// at all, by design (lazy, tiny, no network before the launcher click).
// That means the loader cannot know the tenant's real position
// preference on a visitor's very first-ever page view -- it renders at
// the default ("right") until the chat bundle has loaded once and
// learned the real value.
//
// From the SECOND page view onward, this is fixed: the chat bundle
// caches the confirmed position here (`cache-position.js`'s own
// counterpart in chat/src/), and the loader reads that cached value
// BEFORE it ever creates the launcher, so a returning visitor's
// launcher appears on the correct side from first paint, not just
// after their first click.
//
// Same localStorage-with-in-memory-fallback shape as chat/src/
// session.js's own visitor-secret storage -- a second, independent
// occurrence (loader/ and chat/ are two separate bundles with no
// shared-code wiring today), not a third, so not a duplication-check
// violation; kept deliberately tiny rather than introducing a build-
// time shared-module step for four lines of logic.
const _memoryFallback = new Map();

function _storageKey(siteKey) {
  return `sf-position:${siteKey}`;
}

export function getCachedPosition(siteKey) {
  const key = _storageKey(siteKey);
  let value;
  try {
    value = window.localStorage.getItem(key);
  } catch {
    value = _memoryFallback.get(key) ?? null;
  }
  return value === "left" ? "left" : "right";
}
