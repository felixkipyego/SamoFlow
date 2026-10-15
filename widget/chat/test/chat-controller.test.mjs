import { test } from "node:test";
import assert from "node:assert/strict";
import { ChatController, MAX_ANSWER_LENGTH } from "../src/chat-controller.js";
import { fakeStreamResponse } from "./fake-stream-response.mjs";

function _sessionResponse(token = "tok-1", config = {}) {
  return fakeStreamResponse([
    JSON.stringify({ session_token: token, visitor_secret: null, test_mode: false, widget_config: config }),
  ]);
}

function _sseFrame(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function _makeFetch(handlers) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    for (const [matcher, respond] of handlers) {
      if (matcher(url, init)) {
        return respond(url, init);
      }
    }
    throw new Error(`no handler matched: ${url}`);
  };
  fetchImpl.calls = calls;
  return fetchImpl;
}

const isSession = (url) => url.endsWith("/api/v1/session");
const isChat = (url) => url.endsWith("/api/v1/chat");
const isConversations = (url) => url.endsWith("/api/v1/conversations");

test("a normal turn streams tokens, citations, then done", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [
      isChat,
      () =>
        fakeStreamResponse([
          _sseFrame("token", { text: "Hello" }),
          _sseFrame("citations", { items: [{ n: 1, title: "Doc", url: "https://good.example.com" }] }),
          _sseFrame("done", { message_id: "m1", usage: null, offer_escalation: false }),
        ]),
    ],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [{ conversation_id: "c1" }] })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const events = [];
  await controller.send("hi", {
    onToken: (text) => events.push(["token", text]),
    onCitations: (items) => events.push(["citations", items]),
    onDone: (info) => events.push(["done", info]),
    onError: (msg) => events.push(["error", msg]),
  });

  assert.deepEqual(events[0], ["token", "Hello"]);
  assert.equal(events[1][0], "citations");
  assert.deepEqual(events[2], ["done", { truncated: false, offerEscalation: false }]);
  assert.equal(controller.isStreaming, false);
});

test("a duplicate send() while streaming is a silent no-op: fetch is called only once for /chat", async () => {
  let resolveChat;
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [
      isChat,
      () =>
        new Promise((resolve) => {
          resolveChat = resolve;
        }),
    ],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const first = controller.send("hi", {});
  const second = controller.send("hi again", {}); // should be a no-op, resolves immediately
  await second;
  assert.equal(controller.isStreaming, true);

  // Flushes every pending microtask (session fetch -> json() -> the
  // chat fetch actually being called) so `resolveChat` is guaranteed
  // to have been assigned before it's used below -- a macrotask tick
  // (setTimeout) is the standard way to wait out an unknown number of
  // microtask hops in a test, without coupling the test to the exact
  // await-chain depth inside send()/_sendOnce()/ensureSession().
  await new Promise((resolve) => setTimeout(resolve, 0));

  resolveChat(fakeStreamResponse([_sseFrame("done", { message_id: "m1", usage: null })]));
  await first;

  const chatCalls = fetchImpl.calls.filter((c) => isChat(c.url));
  assert.equal(chatCalls.length, 1);
});

test("an oversized stream is truncated and the reader is cancelled, never grows unbounded", async () => {
  const hugeChunk = "x".repeat(MAX_ANSWER_LENGTH + 5000);
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([_sseFrame("token", { text: hugeChunk })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  let lastToken = null;
  let doneInfo = null;
  await controller.send("hi", {
    onToken: (text) => {
      lastToken = text;
    },
    onDone: (info) => {
      doneInfo = info;
    },
  });

  assert.ok(lastToken.length <= MAX_ANSWER_LENGTH);
  assert.deepEqual(doneInfo, { truncated: true });
});

test("a split SSE frame across two chunks still dispatches exactly once", async () => {
  const whole = _sseFrame("token", { text: "hello world" });
  const splitPoint = Math.floor(whole.length / 2);
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([whole.slice(0, splitPoint), whole.slice(splitPoint)])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const tokens = [];
  await controller.send("hi", { onToken: (text) => tokens.push(text) });
  assert.deepEqual(tokens, ["hello world"]);
});

test("a 401 triggers exactly one session refresh and retry, then succeeds", async () => {
  let sessionCalls = 0;
  let chatCalls = 0;
  const fetchImpl = _makeFetch([
    [
      isSession,
      () => {
        sessionCalls += 1;
        return _sessionResponse(`tok-${sessionCalls}`);
      },
    ],
    [
      isChat,
      () => {
        chatCalls += 1;
        if (chatCalls === 1) {
          return fakeStreamResponse([], { status: 401 });
        }
        return fakeStreamResponse([_sseFrame("done", { message_id: "m1", usage: null })]);
      },
    ],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const events = [];
  await controller.send("hi", { onDone: () => events.push("done"), onError: (m) => events.push(m) });

  assert.equal(chatCalls, 2);
  assert.equal(sessionCalls, 2);
  assert.deepEqual(events, ["done"]);
});

test("a 401 that persists after refresh is a real, calm, fixed failure -- never a retry loop", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([], { status: 401 })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const errors = [];
  await controller.send("hi", { onError: (m) => errors.push(m) });

  const chatCalls = fetchImpl.calls.filter((c) => isChat(c.url));
  assert.equal(chatCalls.length, 2); // one try, one retry, never more
  assert.equal(errors.length, 1);
});

for (const [status, expectedSubstring] of [
  [413, "too long"],
  [429, "quickly"],
  [409, "wait a moment"],
  [503, "unavailable"],
]) {
  test(`a real ${status} response maps to a calm, fixed visitor-facing message`, async () => {
    const fetchImpl = _makeFetch([
      [isSession, () => _sessionResponse()],
      [isChat, () => fakeStreamResponse([], { status })],
    ]);
    const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

    const errors = [];
    await controller.send("hi", { onError: (m) => errors.push(m) });
    assert.equal(errors.length, 1);
    assert.ok(errors[0].toLowerCase().includes(expectedSubstring));
  });
}

test("a 429 with Retry-After includes the real wait time", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([], { status: 429, headers: { "retry-after": "7" } })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const errors = [];
  await controller.send("hi", { onError: (m) => errors.push(m) });
  assert.ok(errors[0].includes("7s"));
});

for (const code of ["model_error", "timeout", "dependency_unavailable"]) {
  test(`the SSE 'error' event code '${code}' maps to a calm, fixed message`, async () => {
    const fetchImpl = _makeFetch([
      [isSession, () => _sessionResponse()],
      [isChat, () => fakeStreamResponse([_sseFrame("error", { code, message: "raw server text" })])],
    ]);
    const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

    const errors = [];
    await controller.send("hi", { onError: (m) => errors.push(m) });
    assert.equal(errors.length, 1);
    assert.notEqual(errors[0], "raw server text"); // the widget's own copy, never the server's raw text
  });
}

test("a dropped connection (stream ends with no 'done' event) surfaces the calm retry message, keeping partial text", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([_sseFrame("token", { text: "partial answer" })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const tokens = [];
  const errors = [];
  await controller.send("hi", { onToken: (t) => tokens.push(t), onError: (m) => errors.push(m) });

  assert.deepEqual(tokens, ["partial answer"]);
  assert.equal(errors.length, 1);
});

test("a network-level fetch failure (offline) surfaces the calm connection-lost message", async () => {
  const fetchImpl = async (url) => {
    if (isSession(url)) return _sessionResponse();
    throw new TypeError("network error");
  };
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const errors = [];
  await controller.send("hi", { onError: (m) => errors.push(m) });
  assert.equal(errors.length, 1);
});

test("the second turn reuses the conversation_id discovered after the first turn's done event", async () => {
  const chatRequests = [];
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [
      isChat,
      (url, init) => {
        chatRequests.push(JSON.parse(init.body));
        return fakeStreamResponse([_sseFrame("done", { message_id: "m1", usage: null })]);
      },
    ],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [{ conversation_id: "real-cid" }] })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  await controller.send("first", {});
  await controller.send("second", {});

  assert.equal(chatRequests[0].conversation_id, null);
  assert.equal(chatRequests[1].conversation_id, "real-cid");
});

test("a malformed (non-JSON) SSE data payload is skipped, never thrown", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse(["event: token\ndata: {not valid json\n\n", _sseFrame("done", { message_id: "m1" })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const events = [];
  await assert.doesNotReject(
    controller.send("hi", { onDone: () => events.push("done"), onToken: () => events.push("token") }),
  );
  assert.deepEqual(events, ["done"]);
});

const isConversationMessages = (url) => /\/api\/v1\/conversations\/[^/]+\/messages$/.test(url);
const isEscalate = (url) => /\/api\/v1\/conversations\/[^/]+\/escalate$/.test(url);

test("retry() resubmits the SAME client_message_id as the turn that just failed", async () => {
  const chatRequests = [];
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [
      isChat,
      (url, init) => {
        chatRequests.push(JSON.parse(init.body));
        return fakeStreamResponse([], { status: 503 });
      },
    ],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  await controller.send("hi", {});
  await controller.retry({});

  assert.equal(chatRequests.length, 2);
  assert.equal(chatRequests[0].client_message_id, chatRequests[1].client_message_id);
  assert.equal(chatRequests[0].message, chatRequests[1].message);
});

test("retry() before any send() is a silent no-op", async () => {
  const fetchImpl = _makeFetch([[isSession, () => _sessionResponse()]]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await assert.doesNotReject(controller.retry({}));
  assert.equal(fetchImpl.calls.length, 0);
});

test("retry() while a turn is already streaming is a silent no-op", async () => {
  let resolveChat;
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => new Promise((resolve) => { resolveChat = resolve; })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const first = controller.send("hi", {});
  await new Promise((resolve) => setTimeout(resolve, 0));
  await controller.retry({});

  const chatCalls = fetchImpl.calls.filter((c) => isChat(c.url));
  assert.equal(chatCalls.length, 1);

  resolveChat(fakeStreamResponse([_sseFrame("done", { message_id: "m1" })]));
  await first;
});

test("listConversations() returns the real list, scoped by the Authorization header", async () => {
  let authHeaderSeen = null;
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse("tok-xyz")],
    [
      isConversations,
      (url, init) => {
        authHeaderSeen = init?.headers?.authorization;
        return fakeStreamResponse([
          JSON.stringify({ conversations: [{ conversation_id: "c1", title: "Hi", created_at: "t", last_message_at: "t" }] }),
        ]);
      },
    ],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const conversations = await controller.listConversations();
  assert.equal(conversations.length, 1);
  assert.equal(conversations[0].conversation_id, "c1");
  assert.equal(authHeaderSeen, "Bearer tok-xyz");
});

test("listConversations() throws on a real server error (caller shows its own error state)", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isConversations, () => fakeStreamResponse([], { status: 503 })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await assert.rejects(controller.listConversations());
});

test("listConversations() on a genuinely empty history returns an empty array, not an error (bad input: empty)", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [] })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  assert.deepEqual(await controller.listConversations(), []);
});

test("loadConversation() returns the real messages and switches conversation_id for the next send()", async () => {
  const chatRequests = [];
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [
      isConversationMessages,
      () => fakeStreamResponse([JSON.stringify({ messages: [{ role: "visitor", text: "hi" }, { role: "assistant", text: "hello" }] })]),
    ],
    [
      isChat,
      (url, init) => {
        chatRequests.push(JSON.parse(init.body));
        return fakeStreamResponse([_sseFrame("done", { message_id: "m1" })]);
      },
    ],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const messages = await controller.loadConversation("old-cid");
  assert.equal(messages.length, 2);

  await controller.send("continuing", {});
  assert.equal(chatRequests[0].conversation_id, "old-cid");
});

test("loadConversation() for a 404 (cross-tenant/unknown cid) throws, never silently empties", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isConversationMessages, () => fakeStreamResponse([], { status: 404 })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await assert.rejects(controller.loadConversation("not-mine"));
});

test("submitEscalation() succeeds and reports the real response", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([_sseFrame("done", { message_id: "m1", offer_escalation: true })])],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [{ conversation_id: "c1" }] })])],
    [isEscalate, () => fakeStreamResponse([JSON.stringify({ escalation_id: "e1", status: "new" })])],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await controller.send("hi", {});

  const submitted = [];
  await controller.submitEscalation({ contact_name: "Ada" }, { onSubmitted: (body) => submitted.push(body) });
  assert.deepEqual(submitted, [{ escalation_id: "e1", status: "new" }]);
});

test("submitEscalation() with no known conversation_id is a calm error, never a crash", async () => {
  const fetchImpl = _makeFetch([[isSession, () => _sessionResponse()]]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });

  const errors = [];
  await controller.submitEscalation({}, { onError: (m) => errors.push(m) });
  assert.equal(errors.length, 1);
});

test("submitEscalation() maps a real 409 (already open) to its own specific calm message", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([_sseFrame("done", { message_id: "m1" })])],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [{ conversation_id: "c1" }] })])],
    [isEscalate, () => fakeStreamResponse([], { status: 409 })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await controller.send("hi", {});

  const errors = [];
  await controller.submitEscalation({}, { onError: (m) => errors.push(m) });
  assert.equal(errors.length, 1);
  assert.ok(errors[0].toLowerCase().includes("already open"));
});

test("submitEscalation() maps a real 429 to the rate-limit message", async () => {
  const fetchImpl = _makeFetch([
    [isSession, () => _sessionResponse()],
    [isChat, () => fakeStreamResponse([_sseFrame("done", { message_id: "m1" })])],
    [isConversations, () => fakeStreamResponse([JSON.stringify({ conversations: [{ conversation_id: "c1" }] })])],
    [isEscalate, () => fakeStreamResponse([], { status: 429, headers: { "retry-after": "30" } })],
  ]);
  const controller = new ChatController("https://api.example.com", "sk_live", { fetchImpl });
  await controller.send("hi", {});

  const errors = [];
  await controller.submitEscalation({}, { onError: (m) => errors.push(m) });
  assert.ok(errors[0].includes("30s"));
});
