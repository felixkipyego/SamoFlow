// Task 5.5: the writer half of loader/src/position-cache.js's own
// cache -- see that file's header for the full reasoning (the loader
// cannot know the tenant's real position preference before its own
// first network call, which only happens once the chat bundle loads
// and fetches widget_config; this cache is what lets a RETURNING
// visitor's launcher start on the correct side from first paint).
const _memoryFallback = new Map();

function _storageKey(siteKey) {
  return `sf-position:${siteKey}`;
}

export function setCachedPosition(siteKey, position) {
  const key = _storageKey(siteKey);
  try {
    window.localStorage.setItem(key, position);
  } catch {
    _memoryFallback.set(key, position);
  }
}
