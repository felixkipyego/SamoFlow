// Task 5.2 [SECURITY]: validates a tenant-supplied color value from
// `widget_config.colors` before it is ever used in an inline style.
//
// Scope deliberately narrow: hex (#rgb, #rgba, #rrggbb, #rrggbbaa) and
// the numeric functional forms (rgb/rgba/hsl/hsla), nothing else --
// explicitly NOT named CSS colors ("red", "rebeccapurple", ...; a large
// table for no real need, since a dashboard color picker emits hex or
// rgb() anyway), NOT `url(...)`, NOT `var(...)`/`calc(...)`, NOT
// `currentColor`/`inherit`. Each pattern is anchored start-to-end
// (`^...$`) with no nested quantifiers over attacker-controlled
// sections, so this cannot be driven into pathological backtracking
// (ReDoS) regardless of input length or shape.
const HEX_PATTERN = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

// One numeric channel: an integer or decimal, optionally signed,
// optionally a percentage -- intentionally permissive on RANGE (CSS
// itself clamps an out-of-range channel; this function's only job is
// to reject anything that is not plainly numeric) and strict on
// CHARACTER CONTENT (nothing else is permitted anywhere in the string).
const NUMBER = "-?\\d+(?:\\.\\d+)?%?";
const RGB_PATTERN = new RegExp(`^rgba?\\(\\s*${NUMBER}\\s*,\\s*${NUMBER}\\s*,\\s*${NUMBER}\\s*(?:,\\s*${NUMBER}\\s*)?\\)$`, "i");
const HSL_PATTERN = new RegExp(`^hsla?\\(\\s*${NUMBER}\\s*,\\s*${NUMBER}\\s*,\\s*${NUMBER}\\s*(?:,\\s*${NUMBER}\\s*)?\\)$`, "i");

export function isValidCssColor(value) {
  if (typeof value !== "string") {
    return false;
  }
  const trimmed = value.trim();
  if (trimmed !== value || trimmed === "") {
    // No leading/trailing whitespace tolerated either: a value that
    // only validates after trimming is not the value that would be
    // used verbatim, and padding is itself a common filter-bypass
    // technique (see url-safety.js's own header) -- reject outright
    // rather than silently normalize.
    return false;
  }
  return HEX_PATTERN.test(trimmed) || RGB_PATTERN.test(trimmed) || HSL_PATTERN.test(trimmed);
}
