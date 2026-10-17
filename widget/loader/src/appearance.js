// [SECURITY] Step 7-follow-up, Part C: fetches the launcher's own
// brand color/position from the new, public, cacheable GET /api/v1/
// widget-appearance endpoint (backend/app/auth/routes.py) -- creates no
// visitor session, no conversation, no LLM call, so calling it eagerly
// here (unlike POST /session, which the loader deliberately never
// calls, Step 5.1's own "lazy, tiny, no network before the launcher
// click" decision) does not reopen that decision's own cost concern.
// This is what lets the launcher show the tenant's REAL color/position
// from the very first paint, on a visitor's very first-ever page view,
// not only after their first click -- the real gap the prior widget
// bug-fix task's own `color-cache.js`/`position-cache.js` localStorage
// mechanism could only ever close for a RETURNING visitor. Both files
// are now retired (not kept alongside this): once the loader has a
// fast, always-correct, server-cached source of truth on every single
// page load, a second, independent, locally-cached approximation of
// the same two values is redundant complexity, not a safety net.
//
// [SECURITY] Validation mirrors the retired color-cache.js's own
// discipline exactly, for the identical reason: this is real,
// network-delivered data, read back only as a CSSOM `setProperty()`
// value (see widget.css's own header for why that is not a markup-
// injection vector either way) -- `primary` must be a strict
// "#" + 6 lowercase hex digits, `primary_text` must be the exact
// literal "#ffffff" or "#000000" (the only two values the backend's
// own `pick_readable_text_color()` ever produces), and `position` must
// be exactly "left" or "right". Anything else -- a malformed/oversized
// response, a network failure, a non-2xx status -- and this returns
// null, falling back to widget.css's own hardcoded defaults exactly
// like a cache miss used to.
const HEX_COLOR = /^#[0-9a-f]{6}$/;

function isValidAppearance(value) {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { primary, primary_text: primaryText, position } = value;
  if (typeof primary !== "string" || !HEX_COLOR.test(primary)) {
    return false;
  }
  if (primaryText !== "#ffffff" && primaryText !== "#000000") {
    return false;
  }
  if (position !== "left" && position !== "right") {
    return false;
  }
  return true;
}

// A slow or unreachable API must never meaningfully delay the
// launcher's own first paint -- bounded here, not left to the
// browser's own (much longer) default fetch timeout.
const FETCH_TIMEOUT_MS = 2000;

export async function fetchAppearance(
  baseUrl,
  siteKey,
  { fetchImpl = (...args) => fetch(...args), timeoutMs = FETCH_TIMEOUT_MS } = {},
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetchImpl(
      `${baseUrl}/api/v1/widget-appearance?site_key=${encodeURIComponent(siteKey)}`,
      { signal: controller.signal },
    );
  } catch {
    return null; // network failure or timeout -- best-effort, never blocks the launcher
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    return null;
  }
  let body;
  try {
    body = await response.json();
  } catch {
    return null;
  }
  if (!isValidAppearance(body)) {
    return null;
  }
  return { primary: body.primary, primaryText: body.primary_text, position: body.position };
}
