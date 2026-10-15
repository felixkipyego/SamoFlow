// Task 5.2: visitor_secret persistence and POST /api/v1/session, per
// docs/SPEC.md §4.3 ("Storage: the visitor secret is kept in the host
// site's localStorage under `aw:{site_key}`. The JWT is held in memory
// only. If storage is blocked, the widget falls back to memory and
// history is lost on refresh.") -- the literal key format and the
// in-memory fallback are both load-bearing spec requirements, not
// stylistic choices.
const _memoryFallback = new Map();

function _storageKey(siteKey) {
  return `aw:${siteKey}`;
}

export function getStoredVisitorSecret(siteKey) {
  const key = _storageKey(siteKey);
  try {
    return window.localStorage.getItem(key);
  } catch {
    return _memoryFallback.get(key) ?? null;
  }
}

export function setStoredVisitorSecret(siteKey, secret) {
  const key = _storageKey(siteKey);
  try {
    window.localStorage.setItem(key, secret);
  } catch {
    _memoryFallback.set(key, secret);
  }
}

// The real, correctly-shaped error: distinguishes a network/transport
// failure from a real HTTP response the server sent (403's own fixed
// body on a bad/disallowed site key, 422 on a malformed request) --
// the caller (chat-controller.js) maps each to a different calm,
// fixed visitor-facing state.
export class SessionRequestFailed extends Error {
  constructor(status) {
    super(`session request failed with status ${status}`);
    this.status = status;
  }
}

// `fetchImpl` is injected (defaults to the real global `fetch`) so
// this is testable with a fake implementation, with no real network
// call and no DOM, matching this step's own established pattern.
//
// The default is a thin wrapper, `(...args) => fetch(...args)`, NOT a
// bare reference to the `fetch` identifier -- a real, found-live bug:
// destructuring/aliasing `fetch` away from `window` detaches it from
// its required `this` binding, and Chromium's native implementation
// throws "Illegal invocation" the moment it is called that way. The
// wrapper calls `fetch(...)` as a genuine global call expression each
// time, which resolves and binds it correctly, exactly like calling it
// directly would.
export async function createSession(baseUrl, siteKey, { fetchImpl = (...args) => fetch(...args) } = {}) {
  const visitorSecret = getStoredVisitorSecret(siteKey);
  const response = await fetchImpl(`${baseUrl}/api/v1/session`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ site_key: siteKey, visitor_secret: visitorSecret }),
  });
  if (!response.ok) {
    throw new SessionRequestFailed(response.status);
  }
  const body = await response.json();
  if (body.visitor_secret) {
    // docs/SPEC.md §4.2: "Return visit: verify the secret and return a
    // fresh JWT" -- visitor_secret is null on a return visit; only a
    // genuinely NEW secret (first visit) is ever (re)persisted.
    setStoredVisitorSecret(siteKey, body.visitor_secret);
  }
  return body;
}
