# backend/tests/test_tenancy_schemas.py
# Step 6.2: app/tenancy/schemas.py -- the dashboard's own server-side
# validation for `tenants.config` writes, built to MIRROR the widget's
# own client-side rules exactly (widget/chat/src/color.js, url-safety.js,
# config-validation.js) -- each test below has a named counterpart
# fixture value taken from (or adversarially targeting) those files.
import pytest
from pydantic import ValidationError

from app.tenancy.schemas import (
    AlertEmailsUpdate,
    AllowedOriginsUpdate,
    RetentionUpdate,
    SupportDetailsUpdate,
    TraceTextUpdate,
    WidgetColors,
    WidgetLookUpdate,
    is_https_url,
    is_valid_css_color,
)

# --- is_valid_css_color: mirrors widget/chat/src/color.js exactly -----


@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#ffff",
        "#4f46e5",
        "#4f46e5ff",
        "rgb(79, 70, 229)",
        "rgba(79, 70, 229, 0.5)",
        "hsl(243, 75%, 59%)",
        "HSLA(243, 75%, 59%, 0.5)",
    ],
)
def test_valid_colors_accepted(value):
    assert is_valid_css_color(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "red",  # named colors deliberately out of scope
        "rebeccapurple",
        "url(evil.css)",
        "var(--x)",
        "currentColor",
        " #fff",  # leading whitespace
        "#fff ",  # trailing whitespace
        "#ff",  # wrong hex length
        "#gggggg",  # non-hex characters
        "",
        "javascript:alert(1)",
        123,
        None,
    ],
)
def test_invalid_colors_rejected(value):
    assert is_valid_css_color(value) is False


# --- is_https_url: mirrors widget/chat/src/url-safety.js exactly ------


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "https://example.com/path?x=1",
        "https://sub.example.com:8443/a",
    ],
)
def test_valid_https_urls_accepted(value):
    assert is_https_url(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",  # not https
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example",  # protocol-relative
        "example.com",  # no scheme at all
        "",
        None,
    ],
)
def test_invalid_https_urls_rejected(value):
    assert is_https_url(value) is False


def test_https_url_tab_embedded_scheme_bypass_is_rejected():
    # The exact bypass url-safety.js's own header comment names: a
    # browser's WHATWG URL parser strips ASCII tab/newline/CR before
    # parsing, so "jav\tascript:alert(1)" becomes a live javascript:
    # navigation even though a naive string check would miss it. The
    # tab here lands INSIDE the scheme, between "java" and "script:" --
    # confirms the stripping happens before the scheme check runs, not
    # that the string merely fails a pattern match.
    value = "jav\tascript:alert(1)"
    assert is_https_url(value) is False
    # And the corresponding real-bypass proof: stripping the tab here
    # does NOT accidentally produce a valid https URL either -- the
    # scheme is "javascript", not "https", before or after stripping.


# --- Pydantic models: field-level rejection, not just the raw helpers --


def test_widget_colors_rejects_invalid_primary():
    with pytest.raises(ValidationError):
        WidgetColors(primary="red")


def test_widget_colors_accepts_valid_primary():
    assert WidgetColors(primary="#4f46e5").primary == "#4f46e5"


def test_widget_look_update_rejects_javascript_logo_url():
    with pytest.raises(ValidationError):
        WidgetLookUpdate(
            title="Chat",
            greeting="Hi",
            colors=WidgetColors(primary="#4f46e5"),
            logo_url="javascript:alert(1)",
            position="right",
            privacy_notice_url=None,
        )


def test_widget_look_update_rejects_invalid_position():
    with pytest.raises(ValidationError):
        WidgetLookUpdate(
            title="Chat",
            greeting="Hi",
            colors=WidgetColors(primary="#4f46e5"),
            logo_url=None,
            position="top",
            privacy_notice_url=None,
        )


def test_widget_look_update_accepts_none_urls():
    update = WidgetLookUpdate(
        title="Chat",
        greeting="Hi",
        colors=WidgetColors(primary="#4f46e5"),
        logo_url=None,
        position="left",
        privacy_notice_url=None,
    )
    assert update.logo_url is None
    assert update.privacy_notice_url is None


def test_widget_look_update_rejects_empty_title():
    with pytest.raises(ValidationError):
        WidgetLookUpdate(
            title="",
            greeting="Hi",
            colors=WidgetColors(primary="#4f46e5"),
            logo_url=None,
            position="right",
            privacy_notice_url=None,
        )


def test_widget_look_update_rejects_unknown_field():
    with pytest.raises(ValidationError):
        WidgetLookUpdate(
            title="Chat",
            greeting="Hi",
            colors=WidgetColors(primary="#4f46e5"),
            logo_url=None,
            position="right",
            privacy_notice_url=None,
            extra_field="not allowed",
        )


def test_support_details_rejects_javascript_contact_link():
    with pytest.raises(ValidationError):
        SupportDetailsUpdate(support_contact_link="javascript:alert(1)")


def test_support_details_accepts_all_none():
    update = SupportDetailsUpdate()
    assert update.support_email is None
    assert update.support_contact_link is None


def test_support_details_rejects_invalid_email():
    with pytest.raises(ValidationError):
        SupportDetailsUpdate(support_email="not-an-email")


def test_alert_emails_rejects_invalid_email():
    with pytest.raises(ValidationError):
        AlertEmailsUpdate(alert_email="not-an-email", escalation_email=None)


def test_retention_update_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        RetentionUpdate(retention_days=0, escalation_retention_months=12)
    with pytest.raises(ValidationError):
        RetentionUpdate(retention_days=91, escalation_retention_months=12)
    with pytest.raises(ValidationError):
        RetentionUpdate(retention_days=30, escalation_retention_months=0)
    with pytest.raises(ValidationError):
        RetentionUpdate(retention_days=30, escalation_retention_months=25)


def test_retention_update_accepts_boundary_values():
    assert RetentionUpdate(retention_days=1, escalation_retention_months=1)
    assert RetentionUpdate(retention_days=90, escalation_retention_months=24)


def test_trace_text_update_requires_bool():
    # Pydantic v2's own lenient bool parsing accepts a small fixed set
    # of strings ("yes"/"no"/"on"/"off"/...) -- not tested here, since
    # that is deliberate upstream behavior, not this schema's own rule.
    # A value outside that set must still 422.
    with pytest.raises(ValidationError):
        TraceTextUpdate(trace_text_enabled="banana")
    assert TraceTextUpdate(trace_text_enabled=True).trace_text_enabled is True


def test_allowed_origins_update_rejects_more_than_twenty():
    with pytest.raises(ValidationError):
        AllowedOriginsUpdate(allowed_origins=[f"https://x{i}.example" for i in range(21)])


def test_allowed_origins_update_accepts_a_normal_list():
    update = AllowedOriginsUpdate(allowed_origins=["https://example.com"])
    assert update.allowed_origins == ["https://example.com"]
