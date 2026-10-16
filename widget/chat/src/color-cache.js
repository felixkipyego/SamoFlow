// Task [ops bugfix]: the writer half of loader/src/color-cache.js's own
// cache -- same shape and same reasoning as position-cache.js's own
// loader/chat split (Task 5.5): the loader never calls the backend, so
// it cannot know the tenant's real primary color before its own first
// network call, which only happens once the chat bundle loads and
// fetches widget_config. This cache is what lets a RETURNING visitor's
// launcher show the correct brand color from first paint, instead of
// the default indigo until their next click.
//
// Stores the ALREADY-COMPUTED `hex`/`text` pair, never the raw
// `config.colors.primary` string a tenant might have saved as rgb()/
// hsl(): the loader must never re-implement `isValidCssColor`'s full
// grammar or `pickReadableTextColor`'s own contrast math (one real
// decision, not duplicated in a second file) -- it only ever reads a
// pre-computed, pre-validated value this module already produced with
// the SAME logic the live header/bubbles/Send button use.
const _memoryFallback = new Map();

function _storageKey(siteKey) {
  return `sf-color:${siteKey}`;
}

export function setCachedColor(siteKey, hex, text) {
  const key = _storageKey(siteKey);
  const value = JSON.stringify({ hex, text });
  try {
    window.localStorage.setItem(key, value);
  } catch {
    _memoryFallback.set(key, value);
  }
}
