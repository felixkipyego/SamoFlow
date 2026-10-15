import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveConfig } from "../src/config.js";

function fakeScript({ siteKey, baseUrl, chatUrl, cssUrl, src } = {}) {
  const attrs = {};
  if (siteKey !== undefined) attrs["data-site-key"] = siteKey;
  if (baseUrl !== undefined) attrs["data-base-url"] = baseUrl;
  if (chatUrl !== undefined) attrs["data-chat-url"] = chatUrl;
  if (cssUrl !== undefined) attrs["data-css-url"] = cssUrl;
  return {
    src: src === undefined ? "https://widget.example.com/widget.js" : src,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name)
        ? attrs[name]
        : null;
    },
  };
}

test("resolves siteKey, baseUrl and the default chatUrl from script attributes", () => {
  const config = resolveConfig(
    fakeScript({ siteKey: "sk_live_abc", baseUrl: "https://api.example.com" }),
  );
  assert.equal(config.error, undefined);
  assert.equal(config.siteKey, "sk_live_abc");
  assert.equal(config.baseUrl, "https://api.example.com");
  assert.equal(config.chatUrl, "https://widget.example.com/chat.js");
  assert.equal(config.cssUrl, "https://widget.example.com/widget.css");
});

test("data-chat-url overrides the default relative-to-script-src chat bundle URL", () => {
  const config = resolveConfig(
    fakeScript({
      siteKey: "sk_live_abc",
      baseUrl: "https://api.example.com",
      chatUrl: "https://cdn.example.com/custom-chat.js",
    }),
  );
  assert.equal(config.chatUrl, "https://cdn.example.com/custom-chat.js");
});

test("data-css-url overrides the default relative-to-script-src stylesheet URL", () => {
  const config = resolveConfig(
    fakeScript({
      siteKey: "sk_live_abc",
      baseUrl: "https://api.example.com",
      cssUrl: "https://cdn.example.com/custom-widget.css",
    }),
  );
  assert.equal(config.cssUrl, "https://cdn.example.com/custom-widget.css");
});

test("errors when data-site-key is missing (expected failure, no default)", () => {
  const config = resolveConfig(fakeScript({ baseUrl: "https://api.example.com" }));
  assert.match(config.error, /data-site-key/);
});

test("treats an empty-string data-site-key as missing (bad input: empty)", () => {
  const config = resolveConfig(
    fakeScript({ siteKey: "", baseUrl: "https://api.example.com" }),
  );
  assert.match(config.error, /data-site-key/);
});

test("errors when data-base-url is missing (decision #6: never defaulted)", () => {
  const config = resolveConfig(fakeScript({ siteKey: "sk_live_abc" }));
  assert.match(config.error, /data-base-url/);
});

test("treats an empty-string data-base-url as missing (bad input: empty)", () => {
  const config = resolveConfig(
    fakeScript({ siteKey: "sk_live_abc", baseUrl: "" }),
  );
  assert.match(config.error, /data-base-url/);
});

test("errors cleanly when the script has no resolvable src and no data-chat-url override (bad input: empty src)", () => {
  const config = resolveConfig(
    fakeScript({
      siteKey: "sk_live_abc",
      baseUrl: "https://api.example.com",
      src: "",
    }),
  );
  assert.match(config.error, /src/);
});

test("errors cleanly when the script's src is not a resolvable URL (bad input: malformed value)", () => {
  const config = resolveConfig(
    fakeScript({
      siteKey: "sk_live_abc",
      baseUrl: "https://api.example.com",
      src: "not a url",
    }),
  );
  assert.match(config.error, /src/);
});
