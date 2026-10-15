// Task 5.2: a minimal fake `Response`-shaped object backing a fake
// `fetch`, so ChatController is fully unit-testable in Node with no
// real network and no DOM -- `chunks` is delivered exactly as given,
// one `reader.read()` resolution per array entry, so a test can split
// an SSE frame across chunk boundaries anywhere it likes.
export function fakeStreamResponse(chunks, { status = 200, headers = {} } = {}) {
  const encoder = new TextEncoder();
  let index = 0;
  let cancelled = false;
  const reader = {
    async read() {
      if (index >= chunks.length) {
        return { done: true, value: undefined };
      }
      const value = encoder.encode(chunks[index]);
      index += 1;
      return { done: false, value };
    },
    async cancel() {
      cancelled = true;
    },
    _wasCancelled: () => cancelled,
  };
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    body: { getReader: () => reader },
    _reader: reader,
    json: async () => JSON.parse(chunks.join("")),
  };
}
