// Task 5.2: the chat panel component. Minimal UI for THIS step's own
// scope (send/receive one real conversation, citations, the calm fixed
// error states, the budget) -- previous-chats (5.3), the escalation
// panel (5.4) and the full WCAG 2.2 AA/theming pass (5.5) are
// deliberately NOT built here, per docs/SPEC.md §18's own step split.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { parseMarkdown } from "./markdown.js";
import { renderMarkdown } from "./render.js";
import { isHttpsUrl } from "./url-safety.js";
import { MAX_MESSAGE_LENGTH } from "./chat-controller.js";

function _updateLast(list, role, updater) {
  const next = list.slice();
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].role === role) {
      next[i] = updater(next[i]);
      break;
    }
  }
  return next;
}

function Citations({ items }) {
  const safeItems = items.filter((item) => isHttpsUrl(item.url));
  if (safeItems.length === 0) {
    return null;
  }
  return h(
    "ul",
    { className: "sf-citations", "aria-label": "Sources" },
    safeItems.map((item) =>
      h(
        "li",
        { key: item.url },
        h("a", { href: item.url, target: "_blank", rel: "noopener noreferrer nofollow" }, item.title),
      ),
    ),
  );
}

function Message({ message }) {
  if (message.role === "visitor") {
    // The visitor's OWN text, rendered the identical safe way as an
    // answer: this is still untrusted content as far as this renderer
    // is concerned (it never special-cases "but I typed it myself").
    return h("div", { className: "sf-message sf-message-visitor" }, renderMarkdown(parseMarkdown(message.text), new Set()));
  }
  const allowedUrls = new Set((message.citations || []).map((c) => c.url));
  return h(
    "div",
    { className: "sf-message sf-message-assistant" },
    renderMarkdown(parseMarkdown(message.text), allowedUrls),
    message.citations?.length ? h(Citations, { items: message.citations }) : null,
  );
}

export function App({ controller, config, launcherEl }) {
  const [open, setOpen] = useState(true);
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [banner, setBanner] = useState(null);
  const listRef = useRef(null);

  useEffect(() => {
    const onToggle = () => setOpen((o) => !o);
    launcherEl.addEventListener("click", onToggle);
    return () => launcherEl.removeEventListener("click", onToggle);
  }, [launcherEl]);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = () => {
    const text = inputValue.trim();
    if (!text || controller.isStreaming) {
      return;
    }
    setInputValue("");
    setBanner(null);
    setMessages((prev) => [
      ...prev,
      { role: "visitor", text },
      { role: "assistant", text: "", citations: [], status: "streaming" },
    ]);

    controller.send(text, {
      onToken: (accumulated) => {
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, text: accumulated })));
      },
      onCitations: (items) => {
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, citations: items })));
      },
      onDone: () => {
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, status: "done" })));
      },
      onError: (message) => {
        setBanner(message);
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, status: "error" })));
      },
    });
  };

  const onInputKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  return h(
    "div",
    { className: "sf-panel", style: { display: open ? "flex" : "none" }, role: "dialog", "aria-label": config.title },
    h(
      "div",
      { className: "sf-header" },
      config.logoUrl ? h("img", { className: "sf-logo", src: config.logoUrl, alt: "" }) : null,
      h("span", { className: "sf-title" }, config.title),
    ),
    h(
      "div",
      { className: "sf-message-list", role: "log", "aria-live": "polite", ref: listRef },
      messages.length === 0 ? h("div", { className: "sf-message sf-message-assistant" }, config.greeting) : null,
      messages.map((message, index) => h(Message, { key: index, message })),
    ),
    banner ? h("div", { className: "sf-banner", role: "status" }, banner) : null,
    h(
      "div",
      { className: "sf-input-row" },
      h("textarea", {
        className: "sf-input",
        value: inputValue,
        maxLength: MAX_MESSAGE_LENGTH,
        disabled: controller.isStreaming,
        "aria-label": "Message",
        onInput: (event) => setInputValue(event.currentTarget.value),
        onKeyDown: onInputKeyDown,
      }),
      h(
        "button",
        { type: "button", className: "sf-send", disabled: controller.isStreaming || inputValue.trim() === "", onClick: handleSend },
        "Send",
      ),
    ),
    config.privacyNoticeUrl
      ? h(
          "a",
          { className: "sf-privacy-link", href: config.privacyNoticeUrl, target: "_blank", rel: "noopener noreferrer nofollow" },
          "Privacy notice",
        )
      : null,
  );
}
