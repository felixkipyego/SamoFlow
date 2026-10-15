// Task 5.4 [SECURITY-adjacent]: client-side mirror of
// `backend/app/handoff/schemas.py`'s own `EscalationRequest` validation
// -- a UX nicety (fail fast, show a field-level error before a round
// trip), never the real enforcement boundary. The server re-validates
// every one of these rules independently and is the only authority; a
// bug or drift in this file can only make the widget reject something
// the server would have accepted (annoying) or accept something the
// server then 422s (an extra round trip) -- it can never let something
// unsafe through, since nothing here is ever trusted on its own.
//
// Rules mirrored exactly, including the control-character rejection
// that specifically covers CR/LF (backend/app/handoff/schemas.py's own
// `_CONTROL_CHARS`, `_LOOKS_LIKE_EMAIL`).
// eslint-disable-next-line no-control-regex -- matching C0 controls + DEL is the whole point of this regex, not an accident the rule should catch
const CONTROL_CHARS = new RegExp("[\u0000-\u001f\u007f]");
const LOOKS_LIKE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const MAX_LENGTHS = { name: 200, email: 320, phone: 40 };

function _validateField(value, field) {
  if (value === "" || value === null || value === undefined) {
    return { value: null, error: null };
  }
  if (value.length > MAX_LENGTHS[field]) {
    return { value, error: `must be ${MAX_LENGTHS[field]} characters or fewer` };
  }
  if (CONTROL_CHARS.test(value)) {
    return { value, error: "must not contain control characters (including line breaks)" };
  }
  if (field === "email" && !LOOKS_LIKE_EMAIL.test(value)) {
    return { value, error: "does not look like a valid email address" };
  }
  return { value, error: null };
}

// Returns {fields: {name, email, phone}, errors: {name?, email?, phone?}}.
// `fields` always holds the three (possibly-null) values regardless of
// validity -- `errors` is the set the UI should actually block submit
// on; an empty `errors` object means the fields are clean.
export function validateContactFields({ name, email, phone } = {}) {
  const nameResult = _validateField(name, "name");
  const emailResult = _validateField(email, "email");
  const phoneResult = _validateField(phone, "phone");

  const errors = {};
  if (nameResult.error) errors.name = nameResult.error;
  if (emailResult.error) errors.email = emailResult.error;
  if (phoneResult.error) errors.phone = phoneResult.error;

  return {
    fields: { name: nameResult.value, email: emailResult.value, phone: phoneResult.value },
    errors,
  };
}
