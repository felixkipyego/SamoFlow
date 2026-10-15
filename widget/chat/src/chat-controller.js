// Task 5.2: stateful chat orchestration, deliberately kept DOM-free and
// Preact-free (fetch + ReadableStream + SSEParser only) so it is fully
// unit-testable in plain Node with an injected fake `fetch`, matching
// this step's own established pattern (pure logic in Node tests; real
// DOM/CSP behavior proven by Playwright instead).
//
// `fetchImpl` defaults to the real global `fetch` -- EventSource cannot
// be used at all here (it is GET-only: no POST body, no custom
// Authorization header), so `fetch()` + its Response body
// `ReadableStream` is the only way to open this stream per docs/SPEC.md
// §10.
import { SSEParser } from "./sse-parser.js";
import { createSession } from "./session.js";

// The real, fixed SSE `error` event codes (backend/app/chat/routes.py:
// `_ERROR_MODEL`/`_ERROR_TIMEOUT`/`_ERROR_DEPENDENCY` -- the third is
// declared but never currently emitted by the server; handled here
// anyway, defensively, since it is part of the documented vocabulary).
// Deliberately NOT using the server's own `message` field verbatim:
// this widget shows its OWN calm, fixed, English copy regardless of
// what the server sends, so a future server-side wording change can
// never silently alter visitor-facing text without a widget review.
const SSE_ERROR_MESSAGES = {
  model_error: "Something went wrong while generating the answer. Please try again.",
  timeout: "That took too long. Please try again.",
  dependency_unavailable: "Something went wrong on our end. Please try again in a moment.",
};
const DEFAULT_SSE_ERROR_MESSAGE = "Something went wrong. Please try again.";

// Client-side mirror of the server's real 2000-character cap
// (`_MESSAGE_CHAR_CAP`, backend/app/chat/routes.py) -- a UX nicety
// only (lets the input show a hint / disable Send before wasting a
// round trip), never the real enforcement boundary: a real 413
// response is still handled below regardless of this constant ever
// drifting out of sync with the server's own.
export const MAX_MESSAGE_LENGTH = 2000;

// Defensive cap on one answer's total accumulated text, independent of
// anything the server is expected to do -- the "oversized stream"
// adversarial case: a malicious or malfunctioning response must not be
// able to grow this component's memory without bound. Once hit, the
// stream is cancelled client-side and the turn ends with whatever text
// had already accumulated, not an error (a long-but-real answer should
// still be shown, truncated, rather than discarded).
export const MAX_ANSWER_LENGTH = 20000;

function _fixedHttpErrorMessage(status, retryAfterHeader) {
  if (status === 401) {
    return "Your session expired. Please reload and try again.";
  }
  if (status === 413) {
    return "That message is too long. Please shorten it and try again.";
  }
  if (status === 429) {
    const seconds = Number.parseInt(retryAfterHeader ?? "", 10);
    return Number.isFinite(seconds) && seconds > 0
      ? `You're sending messages too quickly. Please wait ${seconds}s and try again.`
      : "You're sending messages too quickly. Please wait a moment and try again.";
  }
  if (status === 409) {
    return "Please wait a moment and try again.";
  }
  if (status === 503) {
    return "The service is temporarily unavailable. Please try again in a moment.";
  }
  return "Something went wrong. Please try again.";
}

export class ChatController {
  // See session.js's own header for why the default is a wrapper
  // closure, never a bare `fetch` reference -- the same "Illegal
  // invocation" bug, found live in this step's own Playwright run.
  constructor(baseUrl, siteKey, { fetchImpl = (...args) => fetch(...args) } = {}) {
    this._baseUrl = baseUrl;
    this._siteKey = siteKey;
    this._fetch = fetchImpl;
    this._sessionToken = null; // held in memory ONLY (docs/SPEC.md §4.3)
    this._widgetConfig = null;
    this._conversationId = null;
    this.isStreaming = false;
  }

  async ensureSession() {
    if (this._sessionToken) {
      return this._widgetConfig;
    }
    const body = await createSession(this._baseUrl, this._siteKey, { fetchImpl: this._fetch });
    this._sessionToken = body.session_token;
    this._widgetConfig = body.widget_config;
    return this._widgetConfig;
  }

  // Duplicate-send guard: a second call while one is already in
  // flight is a deliberate, silent no-op -- never queued, never an
  // error surfaced to the caller (the UI itself also disables the
  // send control while streaming; this is the second, independent
  // layer, matching render.js's own "never trust one layer alone"
  // posture).
  async send(messageText, callbacks) {
    if (this.isStreaming) {
      return;
    }
    this.isStreaming = true;
    try {
      await this._sendOnce(messageText, callbacks, { retriedAfterSessionRefresh: false });
    } finally {
      this.isStreaming = false;
    }
  }

  async _sendOnce(messageText, callbacks, { retriedAfterSessionRefresh }) {
    try {
      await this.ensureSession();
    } catch {
      callbacks.onError?.("Couldn't start a session. Please reload and try again.");
      return;
    }

    const clientMessageId = crypto.randomUUID();
    let response;
    try {
      response = await this._fetch(`${this._baseUrl}/api/v1/chat`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: `Bearer ${this._sessionToken}`,
        },
        body: JSON.stringify({
          message: messageText,
          conversation_id: this._conversationId,
          client_message_id: clientMessageId,
        }),
      });
    } catch {
      callbacks.onError?.("Connection lost. Please try again.");
      return;
    }

    if (response.status === 401 && !retriedAfterSessionRefresh) {
      // docs/SPEC.md §4.3: no refresh-token mechanism -- a token nearing
      // or past its lifetime is replaced by calling /session again.
      // Retried exactly once: a 401 that persists after a fresh session
      // is a real, calm, fixed failure, never a retry loop.
      this._sessionToken = null;
      await this._sendOnce(messageText, callbacks, { retriedAfterSessionRefresh: true });
      return;
    }

    if (!response.ok) {
      callbacks.onError?.(_fixedHttpErrorMessage(response.status, response.headers.get("retry-after")));
      return;
    }

    await this._consumeStream(response, callbacks);
  }

  async _consumeStream(response, callbacks) {
    const parser = new SSEParser();
    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let answerText = "";
    let cappedAnswer = false;
    let sawDone = false;

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        const chunkText = decoder.decode(value, { stream: true });
        for (const frame of parser.feed(chunkText)) {
          const data = _parseFrameData(frame.data);
          if (data === null) {
            continue; // a malformed data payload is skipped, never thrown
          }

          if (frame.event === "token") {
            if (!cappedAnswer) {
              answerText += typeof data.text === "string" ? data.text : "";
              if (answerText.length > MAX_ANSWER_LENGTH) {
                answerText = answerText.slice(0, MAX_ANSWER_LENGTH);
                cappedAnswer = true;
              }
              callbacks.onToken?.(answerText);
            }
            if (cappedAnswer) {
              await reader.cancel();
              sawDone = true;
              callbacks.onDone?.({ truncated: true });
              return;
            }
          } else if (frame.event === "citations") {
            const items = Array.isArray(data.items) ? data.items : [];
            callbacks.onCitations?.(items);
          } else if (frame.event === "done") {
            sawDone = true;
            await this._discoverConversationIdIfUnknown();
            callbacks.onDone?.({ truncated: false, offerEscalation: Boolean(data.offer_escalation) });
          } else if (frame.event === "error") {
            const message = SSE_ERROR_MESSAGES[data.code] ?? DEFAULT_SSE_ERROR_MESSAGE;
            callbacks.onError?.(message);
            return;
          }
        }
      }
    } catch {
      callbacks.onError?.("Connection lost. Please try again.");
      return;
    }

    if (!sawDone) {
      // The stream ended (reader signalled done) without a "done"
      // event ever arriving -- a dropped connection, per docs/SPEC.md
      // §10 ("the widget shows a retry button... no partial answer
      // shown as final"). Whatever text DID arrive stays visible
      // (already delivered via onToken above); this only adds the
      // calm failure notice on top of it.
      callbacks.onError?.("Connection lost. Please try again.");
    }
  }

  // Workaround for a real, named contract gap (PROJECT_SPEC.md §9,
  // Step 5.1 Part D): POST /api/v1/chat never returns the
  // conversation_id of a conversation it just created for a first
  // turn (conversation_id: null in the request) -- not in any SSE
  // event, not in a response header. Using only already-locked
  // endpoints: GET /api/v1/conversations is ordered most-recently-
  // active first (`last_message_at` DESC, touched at lease-acquire
  // time -- before the stream even starts), scoped to this visitor's
  // own token, so the conversation this exact turn just touched is
  // reliably conversations[0]. Costs one extra request, only once per
  // browser session (on the first turn only; this._conversationId is
  // cached for every later turn) -- documented, not hidden.
  async _discoverConversationIdIfUnknown() {
    if (this._conversationId !== null) {
      return;
    }
    try {
      const response = await this._fetch(`${this._baseUrl}/api/v1/conversations`, {
        headers: { authorization: `Bearer ${this._sessionToken}` },
      });
      if (!response.ok) {
        return; // best-effort: a failure here just means the NEXT turn also starts a new conversation
      }
      const body = await response.json();
      const first = body.conversations?.[0];
      if (first?.conversation_id) {
        this._conversationId = first.conversation_id;
      }
    } catch {
      // Best-effort, per the comment above -- never surfaced as a
      // visible error for what was otherwise a successfully completed turn.
    }
  }
}

function _parseFrameData(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
