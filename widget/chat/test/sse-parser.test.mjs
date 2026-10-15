import { test } from "node:test";
import assert from "node:assert/strict";
import { SSEParser } from "../src/sse-parser.js";

test("parses one complete event delivered in a single chunk", () => {
  const parser = new SSEParser();
  const frames = parser.feed('event: token\nid: 1\ndata: {"text":"hi"}\n\n');
  assert.deepEqual(frames, [{ event: "token", id: "1", data: '{"text":"hi"}' }]);
});

test("parses multiple events delivered in a single chunk", () => {
  const parser = new SSEParser();
  const frames = parser.feed(
    'event: token\nid: 1\ndata: {"text":"a"}\n\nevent: token\nid: 2\ndata: {"text":"b"}\n\n',
  );
  assert.equal(frames.length, 2);
  assert.equal(frames[0].data, '{"text":"a"}');
  assert.equal(frames[1].data, '{"text":"b"}');
});

test("joins multiple 'data:' lines with a newline, per the SSE spec", () => {
  const parser = new SSEParser();
  const frames = parser.feed("event: token\ndata: line one\ndata: line two\n\n");
  assert.equal(frames[0].data, "line one\nline two");
});

test("ignores comment lines (starting with ':')", () => {
  const parser = new SSEParser();
  const frames = parser.feed(':this is a comment\nevent: token\ndata: hi\n\n');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "token");
});

test("split mid field-name across chunks", () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.feed("ev"), []);
  const frames = parser.feed('ent: token\ndata: {"text":"hi"}\n\n');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "token");
});

test("split mid JSON data value across chunks", () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.feed('event: token\ndata: {"tex'), []);
  const frames = parser.feed('t":"hi"}\n\n');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].data, '{"text":"hi"}');
});

test("split exactly at the blank-line terminator (one \\n then the second \\n)", () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.feed("event: token\ndata: hi\n"), []);
  const frames = parser.feed("\n");
  assert.equal(frames.length, 1);
});

test("a CRLF terminator split exactly between \\r and \\n is not double-counted", () => {
  const parser = new SSEParser();
  // First chunk ends with a lone trailing \r -- must NOT be treated as
  // a complete line terminator yet (it might be the first half of a
  // split \r\n), so nothing is dispatched from this feed() call alone.
  assert.deepEqual(parser.feed("event: token\r\ndata: hi\r"), []);
  const frames = parser.feed("\n\r\n");
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "token");
  assert.equal(frames[0].data, "hi");
});

test("plain CR-only line endings are also supported", () => {
  const parser = new SSEParser();
  // A lone trailing "\r" at the very end of a feed() call is correctly
  // deferred (it might be the first half of a split "\r\n" arriving in
  // the NEXT chunk) -- feeding one further character disambiguates it
  // as a real CR-only terminator, the same way a real second network
  // chunk would.
  assert.deepEqual(parser.feed("event: token\rdata: hi\r\r"), []);
  const frames = parser.feed("x");
  assert.equal(frames.length, 1);
  assert.equal(frames[0].data, "hi");
});

test("a byte-by-byte feed still reconstructs the full event correctly", () => {
  const parser = new SSEParser();
  const raw = 'event: citations\nid: 2\ndata: {"items":[]}\n\n';
  let frames = [];
  for (const ch of raw) {
    frames = frames.concat(parser.feed(ch));
  }
  assert.equal(frames.length, 1);
  assert.equal(frames[0].event, "citations");
  assert.equal(frames[0].data, '{"items":[]}');
});

test("an unrecognized field name is ignored, not treated as data", () => {
  const parser = new SSEParser();
  const frames = parser.feed("event: token\nretry: 1000\ndata: hi\n\n");
  assert.equal(frames[0].data, "hi");
});

test("a value with no leading space after the colon is taken verbatim", () => {
  const parser = new SSEParser();
  const frames = parser.feed("event:token\ndata:hi\n\n");
  assert.equal(frames[0].event, "token");
  assert.equal(frames[0].data, "hi");
});

test("a blank line with nothing accumulated dispatches nothing", () => {
  const parser = new SSEParser();
  const frames = parser.feed("\n\n\n");
  assert.deepEqual(frames, []);
});
