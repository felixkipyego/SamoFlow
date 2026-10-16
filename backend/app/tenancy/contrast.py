# backend/app/tenancy/contrast.py
# [SECURITY] Step 7-follow-up, Part C: a direct Python port of widget/
# chat/src/contrast.js's own WCAG relative-luminance math -- needed here
# so GET /api/v1/widget-appearance (app/auth/routes.py) can compute the
# launcher's own readable text color SERVER-SIDE, once per request
# (cached, see that route's own Cache-Control), rather than asking the
# LOADER to duplicate this logic client-side -- the loader must never
# re-implement contrast math or color parsing (the same rule the prior
# widget bug-fix task's own color-cache.js already established: "the
# loader only ever reads a pre-computed, pre-validated value"). Every
# function below is a line-for-line translation of its own JS
# counterpart; backend/tests/test_contrast.py asserts the SAME input/
# output pairs widget/chat/test/contrast.test.mjs already covers, so a
# future edit to either side that silently diverges fails on sight.
import re

from app.tenancy.schemas import is_valid_css_color

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB = re.compile(r"^rgba?\(([^)]+)\)$", re.IGNORECASE)
_HSL = re.compile(r"^hsla?\(([^)]+)\)$", re.IGNORECASE)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    body = value[1:]
    if len(body) in (3, 4):
        pairs = [c * 2 for c in body[:3]]
    else:
        pairs = [body[i : i + 2] for i in (0, 2, 4)]
    r, g, b = (int(p, 16) for p in pairs)
    return r, g, b


def _channel_to_fraction(raw: str) -> float:
    trimmed = raw.strip()
    if trimmed.endswith("%"):
        return float(trimmed[:-1]) / 100
    return float(trimmed) / 255


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[float, float, float]:  # noqa: E741 (matches contrast.js's own h/s/l names exactly)
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = l - c / 2
    if h < 60:
        r1, g1, b1 = c, x, 0.0
    elif h < 120:
        r1, g1, b1 = x, c, 0.0
    elif h < 180:
        r1, g1, b1 = 0.0, c, x
    elif h < 240:
        r1, g1, b1 = 0.0, x, c
    elif h < 300:
        r1, g1, b1 = x, 0.0, c
    else:
        r1, g1, b1 = c, 0.0, x
    return (r1 + m) * 255, (g1 + m) * 255, (b1 + m) * 255


def _parse_to_rgb_fractions(color: str) -> tuple[float, float, float] | None:
    if color.startswith("#"):
        r, g, b = _hex_to_rgb(color)
        return r / 255, g / 255, b / 255
    rgb_match = _RGB.match(color)
    if rgb_match:
        parts = [p.strip() for p in rgb_match.group(1).split(",")]
        return (
            _channel_to_fraction(parts[0]),
            _channel_to_fraction(parts[1]),
            _channel_to_fraction(parts[2]),
        )
    hsl_match = _HSL.match(color)
    if hsl_match:
        parts = [p.strip() for p in hsl_match.group(1).split(",")]
        h = float(parts[0])
        s = float(parts[1].rstrip("%")) / 100
        l = float(parts[2].rstrip("%")) / 100  # noqa: E741
        r, g, b = _hsl_to_rgb(h, s, l)
        return r / 255, g / 255, b / 255
    return None


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linearize(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(l1: float, l2: float) -> float:
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


_WHITE_LUMINANCE = 1.0
_BLACK_LUMINANCE = 0.0


def pick_readable_text_color(background_color: str) -> str:
    """Returns "#ffffff" or "#000000" -- whichever has the higher real
    contrast ratio against `background_color`, using the real WCAG
    relative-luminance formula. Mirrors widget/chat/src/contrast.js's
    `pickReadableTextColor()` exactly, including its own safe default
    ("#ffffff") for a color this cannot parse.
    """
    rgb = _parse_to_rgb_fractions(background_color)
    if rgb is None:
        return "#ffffff"
    bg_luminance = _relative_luminance(rgb)
    contrast_with_white = _contrast_ratio(bg_luminance, _WHITE_LUMINANCE)
    contrast_with_black = _contrast_ratio(bg_luminance, _BLACK_LUMINANCE)
    return "#ffffff" if contrast_with_white >= contrast_with_black else "#000000"


def to_hex_color(color: str) -> str | None:
    """Canonicalises any color `is_valid_css_color` accepts (hex, rgb/
    rgba, hsl/hsla) to a strict lowercase "#rrggbb" string -- mirrors
    widget/chat/src/contrast.js's `toHexColor()` exactly, reusing the
    SAME parsing `pick_readable_text_color()` above already relies on,
    never a second, independent parser. Returns None for anything this
    cannot parse (should not happen for a value that already passed
    `is_valid_css_color()`, but never raises either way).
    """
    rgb = _parse_to_rgb_fractions(color)
    if rgb is None:
        return None

    def channel(fraction: float) -> str:
        clamped = min(1.0, max(0.0, fraction))
        return format(round(clamped * 255), "02x")

    return f"#{''.join(channel(c) for c in rgb)}"


__all__ = ["is_valid_css_color", "pick_readable_text_color", "to_hex_color"]
