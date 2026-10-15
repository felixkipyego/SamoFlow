// Task 5.4: the escalation ("Talk to a person") consent panel
// (docs/SPEC.md §7: "a panel shows exactly what will be shared... the
// visitor may add optional contact details, and the visitor's browser
// submits POST .../escalate"). The AI never submits anything -- this
// panel is the ONLY caller of `controller.submitEscalation()`.
import { h } from "preact";
import { useState } from "preact/hooks";
import { validateContactFields } from "./escalation-validation.js";

export function EscalationPanel({ controller, onClose, onSubmitted }) {
  const [consentGiven, setConsentGiven] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);

  const handleSubmit = () => {
    // [SECURITY-adjacent] no submission without consent: this is the
    // ONLY gate on the call below -- there is no other path to
    // controller.submitEscalation() in this component at all, so a
    // missing/false consentGiven structurally cannot reach it.
    if (!consentGiven || submitting) {
      return;
    }
    const { fields, errors } = validateContactFields({ name, email, phone });
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    setSubmitting(true);
    setSubmitError(null);
    controller.submitEscalation(
      { contact_name: fields.name, contact_email: fields.email, contact_phone: fields.phone },
      {
        onSubmitted: (body) => {
          setSubmitting(false);
          onSubmitted(body);
        },
        onError: (message) => {
          setSubmitting(false);
          setSubmitError(message);
        },
      },
    );
  };

  return h(
    "div",
    { className: "sf-escalation-panel", role: "dialog", "aria-label": "Talk to a person" },
    h("h2", null, "Talk to a person"),
    h(
      "p",
      { className: "sf-escalation-consent" },
      "This will share your conversation so far, and any contact details you add below, with a team member at this organization. Nothing is sent until you confirm below.",
    ),
    h(
      "div",
      { className: "sf-field-row" },
      h("label", { htmlFor: "sf-esc-name" }, "Name (optional)"),
      h("input", {
        id: "sf-esc-name",
        type: "text",
        value: name,
        onInput: (event) => setName(event.currentTarget.value),
        "aria-invalid": Boolean(fieldErrors.name),
      }),
      fieldErrors.name ? h("span", { className: "sf-field-error", role: "alert" }, fieldErrors.name) : null,
    ),
    h(
      "div",
      { className: "sf-field-row" },
      h("label", { htmlFor: "sf-esc-email" }, "Email (optional)"),
      h("input", {
        id: "sf-esc-email",
        type: "email",
        value: email,
        onInput: (event) => setEmail(event.currentTarget.value),
        "aria-invalid": Boolean(fieldErrors.email),
      }),
      fieldErrors.email ? h("span", { className: "sf-field-error", role: "alert" }, fieldErrors.email) : null,
    ),
    h(
      "div",
      { className: "sf-field-row" },
      h("label", { htmlFor: "sf-esc-phone" }, "Phone (optional)"),
      h("input", {
        id: "sf-esc-phone",
        type: "tel",
        value: phone,
        onInput: (event) => setPhone(event.currentTarget.value),
        "aria-invalid": Boolean(fieldErrors.phone),
      }),
      fieldErrors.phone ? h("span", { className: "sf-field-error", role: "alert" }, fieldErrors.phone) : null,
    ),
    h(
      "div",
      { className: "sf-field-row" },
      h(
        "label",
        null,
        h("input", {
          type: "checkbox",
          checked: consentGiven,
          onChange: (event) => setConsentGiven(event.currentTarget.checked),
        }),
        " I agree to share this with a team member",
      ),
    ),
    submitError ? h("p", { className: "sf-banner", role: "status" }, submitError) : null,
    h(
      "div",
      { className: "sf-escalation-actions" },
      h(
        "button",
        {
          type: "button",
          className: "sf-escalation-submit",
          disabled: !consentGiven || submitting,
          onClick: handleSubmit,
        },
        submitting ? "Sending…" : "Send",
      ),
      h("button", { type: "button", className: "sf-escalation-cancel", onClick: onClose }, "Cancel"),
    ),
  );
}
