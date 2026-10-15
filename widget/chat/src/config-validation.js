// Task 5.2 [SECURITY]: `widget_config` comes from `POST /api/v1/session`,
// built server-side from the tenant's own JSONB `config` column
// (`_public_widget_config()`, `backend/app/auth/routes.py`) -- a tenant
// administrator's input, not SamoFlow's own code, and therefore
// untrusted the same way answer text is. None of its six keys has a
// schema locked anywhere yet (Step 5.1 Part C's own contract check,
// PROJECT_SPEC.md §9: "this task's own choice, not a spec-mandated wire
// contract") -- this function is this component's own defensive
// boundary regardless of what shape eventually arrives.
//
// Every field either validates or falls back to a safe default; never
// throws, never half-applies one tenant-supplied value while silently
// trusting an adjacent unvalidated one.
import { isValidCssColor } from "./color.js";
import { isHttpsUrl } from "./url-safety.js";

const DEFAULT_PRIMARY_COLOR = "#4f46e5";

function _asText(value, fallback) {
  // title/greeting are rendered as TEXT content (a plain Preact string
  // child), never HTML, regardless of what this returns -- this only
  // guards against a non-string value (null, a number, an object) and
  // an unreasonable length, not against markup (there is no markup
  // concern here at all; see render.js's own header for why a plain
  // string child can never become live markup).
  if (typeof value !== "string" || value.trim() === "") {
    return fallback;
  }
  const MAX_LENGTH = 200;
  return value.length > MAX_LENGTH ? value.slice(0, MAX_LENGTH) : value;
}

function _validatedHttpsUrl(value) {
  // Rejects javascript:/data:/vbscript: and every other non-https
  // scheme by construction (isHttpsUrl), and protocol-relative/relative
  // values too (no base is ever supplied) -- the identical rule
  // applied to answer-text links in render.js.
  return isHttpsUrl(value) ? value : null;
}

function _validatedColors(rawColors) {
  if (typeof rawColors !== "object" || rawColors === null) {
    return { primary: DEFAULT_PRIMARY_COLOR };
  }
  const primary = isValidCssColor(rawColors.primary) ? rawColors.primary : DEFAULT_PRIMARY_COLOR;
  return { primary };
}

export function validateWidgetConfig(rawConfig) {
  const raw = typeof rawConfig === "object" && rawConfig !== null ? rawConfig : {};
  return {
    title: _asText(raw.title, "Chat"),
    greeting: _asText(raw.greeting, "Hi! How can we help?"),
    colors: _validatedColors(raw.colors),
    logoUrl: _validatedHttpsUrl(raw.logo_url),
    privacyNoticeUrl: _validatedHttpsUrl(raw.privacy_notice_url),
  };
}
