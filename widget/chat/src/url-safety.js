// Task 5.2 [SECURITY]: the one real gate between untrusted text (a
// model's answer, a tenant's config) and a live `href`/`src`.
//
// Deliberately uses the real `URL` constructor, never a hand-rolled
// `startswith("javascript:")`-style string check: browsers strip
// certain ASCII whitespace/control characters (tab, newline, carriage
// return) from a URL while parsing it, which is exactly how a scheme
// check like `"jav\tascript:alert(1)".startsWith("javascript:")`
// (false) can miss a URL the browser's own navigation will still treat
// as `javascript:` once it normalizes the string -- using the SAME
// WHATWG URL parser the browser itself uses for navigation closes this
// gap by construction, not by trying to enumerate every whitespace/
// control-character trick by hand.
//
// Called with NO base argument, deliberately: a protocol-relative
// (`//evil.example`) or scheme-relative/relative URL has no scheme of
// its own, so `new URL(value)` throws -- it is rejected outright,
// never silently resolved against some assumed base origin.
export function isHttpsUrl(value) {
  if (typeof value !== "string" || value === "") {
    return false;
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return parsed.protocol === "https:";
}

// PART A [SECURITY] (verified_domains link allowlist, docs/
// DECISIONS_REGISTER.md's "Widget track close-out" forward commitment):
// a single trailing dot ("verified.example.") is a different STRING
// from its own non-dotted form but the SAME real DNS name -- stripped
// on BOTH sides of every comparison below so neither side's own
// spelling choice ever causes a false negative. Never stripped more
// than once: a real hostname cannot have two in a row (the URL/host
// parser itself would already reject an empty label).
function _stripOneTrailingDot(hostname) {
  return hostname.endsWith(".") ? hostname.slice(0, -1) : hostname;
}

// The exact hostname a real https:// navigation would use, canonicalised
// the SAME way on every call site below (lowercase, IDN already
// converted to its punycode/ASCII form by the real WHATWG URL parser --
// never a hand-rolled IDNA implementation) -- `null` for anything that
// does not even parse as a URL at all, never a thrown exception a
// caller might forget to catch.
export function urlHostname(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  return _stripOneTrailingDot(parsed.hostname);
}

// Server-side `normalize_domain()` (backend/app/ingest/domain_
// verification.py) stores a BARE hostname only (confirmed by reading
// its own docstring and callers) -- lowercase, no scheme, no port, no
// path, no wildcard. This is the widget's OWN defensive mirror of that
// same shape check (never trust the network blindly even though the
// server already validates): anything containing whitespace, "*", "/",
// ":", "@" or a backslash is rejected outright, BEFORE the remaining
// string is ever handed to `new URL()` for IDN canonicalisation -- the
// order matters: `new URL("https://" + "evil.com/x" + "/")` would
// happily absorb "/x" into `.pathname` and still return a plausible-
// looking `.hostname`, silently "fixing" a value shaped unlike anything
// `normalize_domain()` would ever have actually stored, rather than
// rejecting it the way a truly malformed/tampered value should be.
const _DOMAIN_MAX_LENGTH = 253; // the real DNS name length limit
const _DISALLOWED_DOMAIN_CHARS = /[\s*/:@\\]/;

export function canonicalizeVerifiedDomain(raw) {
  if (typeof raw !== "string") {
    return null;
  }
  const trimmed = raw.trim();
  if (trimmed === "" || trimmed.length > _DOMAIN_MAX_LENGTH) {
    return null;
  }
  if (_DISALLOWED_DOMAIN_CHARS.test(trimmed)) {
    return null;
  }
  let parsed;
  try {
    parsed = new URL(`https://${trimmed}/`);
  } catch {
    return null;
  }
  return _stripOneTrailingDot(parsed.hostname);
}
