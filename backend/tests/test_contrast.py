# backend/tests/test_contrast.py
# [SECURITY] Step 7-follow-up, Part C: the same test VECTORS widget/
# chat/test/contrast.test.mjs already covers, run against the Python
# port (app/tenancy/contrast.py) -- if either side's own algorithm ever
# silently diverges from the other, this (or that file) fails on sight,
# not years later as a subtly-wrong launcher text color on some real
# tenant's own site.
import pytest

from app.tenancy.contrast import pick_readable_text_color, to_hex_color


def test_a_dark_background_picks_white_text():
    assert pick_readable_text_color("#000000") == "#ffffff"
    assert pick_readable_text_color("#1a1a1a") == "#ffffff"


def test_a_light_background_picks_black_text():
    assert pick_readable_text_color("#ffffff") == "#000000"
    assert pick_readable_text_color("#f5f5f5") == "#000000"


def test_a_mid_tone_brand_color_picks_white():
    assert pick_readable_text_color("#4f46e5") == "#ffffff"


def test_3_digit_and_4_digit_hex_shorthand_both_parse_correctly():
    assert pick_readable_text_color("#000") == "#ffffff"
    assert pick_readable_text_color("#fff") == "#000000"
    assert pick_readable_text_color("#000f") == "#ffffff"


def test_rgb_rgba_forms_parse_and_pick_correctly():
    assert pick_readable_text_color("rgb(0, 0, 0)") == "#ffffff"
    assert pick_readable_text_color("rgba(255, 255, 255, 0.5)") == "#000000"


def test_hsl_hsla_forms_parse_and_pick_correctly():
    assert pick_readable_text_color("hsl(0, 0%, 0%)") == "#ffffff"  # black
    assert pick_readable_text_color("hsl(0, 0%, 100%)") == "#000000"  # white


def test_a_very_light_tenant_color_picks_black_text():
    assert pick_readable_text_color("#ffff99") == "#000000"


def test_a_very_dark_tenant_color_picks_white_text():
    assert pick_readable_text_color("#111133") == "#ffffff"


@pytest.mark.parametrize(
    "color",
    ["#4f46e5", "#e53e3e", "#38a169", "#1a202c", "#f7fafc", "#805ad5", "#d69e2e", "#2b6cb0"],
)
def test_the_chosen_color_always_meets_at_least_wcag_aa_4_5_1(color):
    text = pick_readable_text_color(color)
    hex_value = color.lstrip("#")
    r, g, b = (int(hex_value[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def linearize(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)
    text_luminance = 1.0 if text == "#ffffff" else 0.0
    ratio = (max(luminance, text_luminance) + 0.05) / (min(luminance, text_luminance) + 0.05)
    assert ratio >= 4.5, f"{color} -> {text} only reaches {ratio:.2f}:1"


def test_an_unparseable_color_never_raises_falls_back_to_a_safe_default():
    assert pick_readable_text_color("not-a-color") == "#ffffff"


def test_to_hex_color_canonicalises_hex_rgb_hsl_inputs_to_a_lowercase_rrggbb_string():
    assert to_hex_color("#14b8a6") == "#14b8a6"
    assert to_hex_color("#4F46E5") == "#4f46e5"
    assert to_hex_color("#fff") == "#ffffff"
    assert to_hex_color("rgb(20, 184, 166)") == "#14b8a6"
    assert to_hex_color("hsl(0, 0%, 0%)") == "#000000"


def test_to_hex_color_returns_none_never_raises_for_an_unparseable_color():
    assert to_hex_color("not-a-color") is None
    to_hex_color("javascript:alert(1)")  # must not raise
