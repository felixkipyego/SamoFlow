// Task 5.2: an incremental Server-Sent-Events frame parser, robust across
// arbitrary chunk boundaries -- `fetch()` + `ReadableStream` (required:
// EventSource is GET-only and cannot carry the Authorization header or a
// POST body) gives bytes in whatever chunking the network happens to
// produce, which may split a frame anywhere at all: mid field-name, mid
// JSON value, or exactly inside a CRLF terminator.
//
// Pure and DOM-free: feed() takes a plain string and returns plain
// objects, so this is fully unit-testable in Node with no browser.
//
// Per the SSE spec (https://html.spec.whatwg.org/multipage/server-
// sent-events.html#parsing-an-event-stream): lines end in LF, CR or
// CRLF; a line starting with ":" is a comment (ignored); "field:value"
// lines accumulate onto the current event (multiple "data:" lines join
// with "\n"); a blank line dispatches the accumulated event.
export class SSEParser {
  constructor() {
    this._buffer = "";
    this._event = null;
    this._dataLines = [];
    this._id = null;
  }

  // Returns an array of {event, id, data} frames completed by this
  // chunk (zero, one, or several -- a single chunk may contain multiple
  // full events, or none at all if it only completes a partial line).
  feed(chunk) {
    this._buffer += chunk;
    const frames = [];

    for (;;) {
      const cut = this._nextLineEnd(this._buffer);
      if (cut === null) {
        // No complete line terminator yet (including the deliberately
        // deferred case: the buffer ends in a lone "\r" that might be
        // the first half of a split "\r\n" -- wait for more data rather
        // than guessing).
        break;
      }
      const line = this._buffer.slice(0, cut.lineEnd);
      this._buffer = this._buffer.slice(cut.next);

      if (line === "") {
        const frame = this._dispatch();
        if (frame) {
          frames.push(frame);
        }
        continue;
      }
      if (line.startsWith(":")) {
        continue; // a comment line, per spec, carries no data at all
      }
      this._consumeField(line);
    }

    return frames;
  }

  // Finds the first line terminator in `buffer`, returning
  // {lineEnd, next} (lineEnd: index where the line's own text ends;
  // next: index just past the terminator) or null if none is found yet
  // OR the only candidate is a trailing lone "\r" that needs more data
  // to disambiguate from a split "\r\n".
  _nextLineEnd(buffer) {
    for (let i = 0; i < buffer.length; i += 1) {
      const ch = buffer[i];
      if (ch === "\n") {
        return { lineEnd: i, next: i + 1 };
      }
      if (ch === "\r") {
        if (i + 1 < buffer.length) {
          const next = buffer[i + 1] === "\n" ? i + 2 : i + 1;
          return { lineEnd: i, next };
        }
        return null; // lone trailing \r -- wait for more data
      }
    }
    return null;
  }

  _consumeField(line) {
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1); // exactly one leading space is stripped, per spec
    }

    if (field === "event") {
      this._event = value;
    } else if (field === "data") {
      this._dataLines.push(value);
    } else if (field === "id") {
      this._id = value;
    }
    // "retry" and any other field name: not used by this contract, ignored.
  }

  _dispatch() {
    if (this._event === null && this._dataLines.length === 0 && this._id === null) {
      return null; // a blank line with nothing accumulated dispatches nothing
    }
    const frame = {
      event: this._event,
      id: this._id,
      data: this._dataLines.join("\n"),
    };
    this._event = null;
    this._dataLines = [];
    this._id = null;
    return frame;
  }
}
