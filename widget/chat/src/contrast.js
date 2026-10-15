// Task 5.5: automatic text-colour contrast against the tenant's own
// primary color (docs/SPEC.md §11 design direction: "the platform
// computes the text color on the primary color (black or white), which
// always meets WCAG AA"). Operates only on a value `isValidCssColor`
// (color.js) has already accepted -- a tenant can never make text
// unreadable by picking a bad primary color, because this always picks
// whichever of black/white has the higher real contrast against it,
// using the actual WCAG relative-luminance formula, not a brightness
// guess.
//
// Parses every form `isValidCssColor` accepts (hex, rgb/rgba, hsl/hsla)
// since contrast math needs real RGB channels regardless of which
// syntax the tenant's color happens to use.
function _hexToRgb(hex) {
  const value = hex.slice(1);
  const expand = value.length === 3 || value.length === 4;
  const pairs = expand ? [...value].map((c) => c + c) : value.match(/.{2}/g);
  const [r, g, b] = pairs.slice(0, 3).map((p) => Number.parseInt(p, 16));
  return [r, g, b];
}

function _channelToFraction(raw) {
  const trimmed = raw.trim();
  if (trimmed.endsWith("%")) {
    return Number.parseFloat(trimmed) / 100;
  }
  return Number.parseFloat(trimmed) / 255;
}

function _hslToRgb(h, s, l) {
  // Standard HSL->RGB conversion (h in degrees, s/l as 0..1 fractions).
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r1;
  let g1;
  let b1;
  if (h < 60) [r1, g1, b1] = [c, x, 0];
  else if (h < 120) [r1, g1, b1] = [x, c, 0];
  else if (h < 180) [r1, g1, b1] = [0, c, x];
  else if (h < 240) [r1, g1, b1] = [0, x, c];
  else if (h < 300) [r1, g1, b1] = [x, 0, c];
  else [r1, g1, b1] = [c, 0, x];
  return [(r1 + m) * 255, (g1 + m) * 255, (b1 + m) * 255];
}

function _parseToRgbFractions(color) {
  if (color.startsWith("#")) {
    const [r, g, b] = _hexToRgb(color);
    return [r / 255, g / 255, b / 255];
  }
  const rgbMatch = /^rgba?\(([^)]+)\)$/i.exec(color);
  if (rgbMatch) {
    const parts = rgbMatch[1].split(",").map((p) => p.trim());
    return [_channelToFraction(parts[0]), _channelToFraction(parts[1]), _channelToFraction(parts[2])];
  }
  const hslMatch = /^hsla?\(([^)]+)\)$/i.exec(color);
  if (hslMatch) {
    const parts = hslMatch[1].split(",").map((p) => p.trim());
    const h = Number.parseFloat(parts[0]);
    const s = Number.parseFloat(parts[1]) / 100;
    const l = Number.parseFloat(parts[2]) / 100;
    const [r, g, b] = _hslToRgb(h, s, l);
    return [r / 255, g / 255, b / 255];
  }
  return null;
}

function _relativeLuminance([r, g, b]) {
  // WCAG 2.x relative luminance formula (the real, specified one -- not
  // a brightness/average approximation).
  const linearize = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [rl, gl, bl] = [r, g, b].map(linearize);
  return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl;
}

function _contrastRatio(l1, l2) {
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

const WHITE_LUMINANCE = 1;
const BLACK_LUMINANCE = 0;

// Returns "#ffffff" or "#000000" -- whichever has the higher real
// contrast ratio against `backgroundColor`. Defaults to "#ffffff" (the
// safer default against this component's own dark-ish default primary,
// #4f46e5) if the color cannot be parsed at all -- should not happen
// for anything that already passed `isValidCssColor`, but never throws
// either way.
export function pickReadableTextColor(backgroundColor) {
  const rgb = _parseToRgbFractions(backgroundColor);
  if (rgb === null) {
    return "#ffffff";
  }
  const bgLuminance = _relativeLuminance(rgb);
  const contrastWithWhite = _contrastRatio(bgLuminance, WHITE_LUMINANCE);
  const contrastWithBlack = _contrastRatio(bgLuminance, BLACK_LUMINANCE);
  return contrastWithWhite >= contrastWithBlack ? "#ffffff" : "#000000";
}
