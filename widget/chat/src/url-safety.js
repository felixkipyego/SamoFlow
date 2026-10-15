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
