import { test } from "node:test";
import assert from "node:assert/strict";
import { decodeEntities } from "../src/entities.js";

test("decodes the five supported named entities", () => {
  assert.equal(decodeEntities("a &amp; b"), "a & b");
  assert.equal(decodeEntities("5 &lt; 10"), "5 < 10");
  assert.equal(decodeEntities("10 &gt; 5"), "10 > 5");
  assert.equal(decodeEntities("&quot;hi&quot;"), '"hi"');
  assert.equal(decodeEntities("it&apos;s"), "it's");
});

test("decodes decimal numeric character references", () => {
  assert.equal(decodeEntities("&#106;avascript"), "javascript");
});

test("decodes hex numeric character references (both x and X)", () => {
  assert.equal(decodeEntities("&#x6A;avascript"), "javascript");
  assert.equal(decodeEntities("&#X6A;avascript"), "javascript");
});

test("leaves an unrecognized named entity exactly as written", () => {
  assert.equal(decodeEntities("&hearts;"), "&hearts;");
});

test("replaces an invalid/out-of-range code point with U+FFFD", () => {
  assert.equal(decodeEntities("&#0;"), "�");
  assert.equal(decodeEntities("&#x110000;"), "�");
  assert.equal(decodeEntities("&#xD800;"), "�"); // a lone surrogate
});

test("leaves plain text with no entities untouched", () => {
  assert.equal(decodeEntities("plain text, no entities here"), "plain text, no entities here");
});
