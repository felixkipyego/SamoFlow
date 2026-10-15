import { test } from "node:test";
import assert from "node:assert/strict";
import { validateContactFields } from "../src/escalation-validation.js";

test("empty/omitted fields are valid (all optional)", () => {
  const { fields, errors } = validateContactFields({});
  assert.deepEqual(fields, { name: null, email: null, phone: null });
  assert.deepEqual(errors, {});
});

test("valid fields pass through unchanged", () => {
  const { fields, errors } = validateContactFields({
    name: "Ada Lovelace",
    email: "ada@example.com",
    phone: "+1 555 0100",
  });
  assert.deepEqual(fields, { name: "Ada Lovelace", email: "ada@example.com", phone: "+1 555 0100" });
  assert.deepEqual(errors, {});
});

test("rejects an embedded CR or LF in any field -- the real injection-adjacent case", () => {
  const { errors: nameErrors } = validateContactFields({ name: "Evil\r\nBcc: attacker@evil.example" });
  assert.ok(nameErrors.name);

  const { errors: emailErrors } = validateContactFields({ email: "a@example.com\nBcc: x@evil.example" });
  assert.ok(emailErrors.email);

  const { errors: phoneErrors } = validateContactFields({ phone: "555\r\n0100" });
  assert.ok(phoneErrors.phone);
});

test("rejects other control characters too, not just CR/LF", () => {
  const { errors } = validateContactFields({ name: "Ada\x00Lovelace" });
  assert.ok(errors.name);
});

test("rejects an email that doesn't look like one", () => {
  const { errors } = validateContactFields({ email: "not-an-email" });
  assert.ok(errors.email);
});

test("enforces the exact server-side max lengths (bad input: oversized)", () => {
  assert.ok(validateContactFields({ name: "x".repeat(201) }).errors.name);
  assert.ok(!validateContactFields({ name: "x".repeat(200) }).errors.name);
  assert.ok(validateContactFields({ email: `${"x".repeat(312)}@example.com` }).errors.email);
  assert.ok(validateContactFields({ phone: "1".repeat(41) }).errors.phone);
  assert.ok(!validateContactFields({ phone: "1".repeat(40) }).errors.phone);
});

test("never throws on null/undefined input (bad input: None)", () => {
  assert.doesNotThrow(() => validateContactFields());
  assert.doesNotThrow(() => validateContactFields(undefined));
});
