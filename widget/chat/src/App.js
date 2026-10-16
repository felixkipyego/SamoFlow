// Task 5.2-5.5: the chat panel component.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { parseMarkdown } from "./markdown.js";
import { renderMarkdown } from "./render.js";
import { isHttpsUrl } from "./url-safety.js";
import { MAX_MESSAGE_LENGTH } from "./chat-controller.js";
import { ConversationList } from "./ConversationList.js";
import { EscalationPanel } from "./EscalationPanel.js";

const MOBILE_SHEET_QUERY = "(max-width: 480px)";

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

// Task 5.3: history has no PER-TURN citation set to gate against (the
// register's own "Unscheduled" entry: past-turn citations can't be
// reconstructed from conversation history, a per-turn-overwrite state
// field) -- passing an EMPTY allow-set into the SAME renderer every
// live message already uses means any `[text](url)` surviving in
// stored text structurally can never match, and therefore always
// renders as plain text, never a live link. No separate "history mode"
// branch in the renderer itself; the empty set alone is what does it.
const NO_CITATIONS_ALLOWED = new Set();

function Message({ message, onRetry, verifiedDomains }) {
  if (message.role === "visitor") {
    // PART A: the visitor's OWN typed text never gets a live link
    // either way, same reasoning NO_CITATIONS_ALLOWED already has --
    // this is the visitor's own unvetted input, not tenant-controlled
    // content, so it is never compared against verified_domains at
    // all (a bare 2-arg call; render.js's own default parameter value
    // for verifiedDomains is an empty set).
    return h("div", { className: "sf-message sf-message-visitor" }, renderMarkdown(parseMarkdown(message.text), NO_CITATIONS_ALLOWED));
  }
  const allowedUrls = new Set((message.citations || []).map((c) => c.url));
  return h(
    "div",
    { className: `sf-message sf-message-assistant${message.status === "error" ? " sf-message-error" : ""}` },
    // PART A: verifiedDomains is a TENANT-wide, session-long property
    // (unlike citations, which are genuinely per-turn and cannot be
    // reconstructed for history, docs/DECISIONS_REGISTER.md's own
    // "Unscheduled" entry) -- so it applies identically to a LIVE
    // answer and a past, re-opened conversation's own stored text, no
    // separate "history mode" needed here either.
    renderMarkdown(parseMarkdown(message.text), allowedUrls, verifiedDomains),
    // Part D (docs/SPEC.md §9's "Stop mid-stream"): a visible, plain-text
    // marker on a turn the visitor stopped early -- never styled to look
    // like part of the model's own answer text (a separate element, not
    // concatenated into `message.text`), so it can never be mistaken for
    // real streamed content.
    message.status === "stopped" ? h("span", { className: "sf-stopped-indicator" }, " (stopped)") : null,
    message.citations?.length ? h(Citations, { items: message.citations }) : null,
    message.status === "error" && onRetry
      ? h("button", { type: "button", className: "sf-retry", onClick: onRetry }, "Retry")
      : null,
  );
}

export function App({ controller, config, launcherEl }) {
  // PART A: computed once per render, not memoized -- `config.
  // verifiedDomains` is a short, already-validated (config-
  // validation.js) array that never changes within one mounted
  // session, so the cost of rebuilding this Set on every render is
  // negligible; memoizing it would be complexity this component does
  // not need (rule 11).
  const verifiedDomains = new Set(config.verifiedDomains || []);
  const [open, setOpen] = useState(true);
  const [view, setView] = useState("chat"); // "chat" | "conversations" | "escalation"
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [banner, setBanner] = useState(null);
  const [offerEscalation, setOfferEscalation] = useState(false);
  const [escalationSubmitted, setEscalationSubmitted] = useState(false);
  // [ops bugfix]: the real bug behind "only one message can be sent".
  // `controller.isStreaming` is a plain mutable property on a class
  // instance, not Preact state -- reading it directly here (as the
  // `disabled` prop below used to) can never, by itself, cause a
  // re-render when it changes. The LAST re-render during a turn is
  // triggered by `onDone`/`onError` calling `setMessages`/`setBanner`
  // from INSIDE `_consumeStream` -- which runs, and finishes, BEFORE
  // `ChatController.send()`'s/`retry()`'s own `finally` block ever sets
  // `isStreaming = false`. So that render reads `controller.isStreaming`
  // as still `true`, and nothing ever renders again afterward: the input
  // stays disabled forever, confirmed live against the real backend.
  // This component now tracks its OWN reactive copy instead, set in
  // `runTurn()` below around the exact same call, so Preact always gets
  // a render when it flips back.
  const [isStreaming, setIsStreaming] = useState(false);
  const listRef = useRef(null);
  const panelRef = useRef(null);
  const liveRegionRef = useRef(null);
  const textareaRef = useRef(null);

  // [ops bugfix]: tenant branding (--sf-primary/--sf-primary-text) is no
  // longer set here -- mount.js now sets it once, imperatively, on
  // `hostEl` (the shadow host shared by the launcher AND this panel),
  // before this component ever renders. Setting it on `panelRef` alone,
  // as this effect used to, scoped the custom properties to the panel's
  // own subtree only -- the launcher button is a SIBLING of the panel
  // under that same shadow root, not a descendant of it, so it never
  // inherited the color at all. See mount.js's own header for the fix.

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

  // Task 5.5: Esc always closes (desktop non-modal AND the mobile
  // sheet, docs/SPEC.md §11's own design direction); on the mobile
  // sheet specifically (the full-screen view below 480px), the panel
  // is modal while open -- a real focus trap (Tab/Shift+Tab never
  // leaves the panel) and the host page's own scroll locked -- whereas
  // the desktop panel stays non-modal (no trap, host page usable
  // around it). Focus always returns to the launcher on close, either way.
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const isMobileSheet = () => window.matchMedia(MOBILE_SHEET_QUERY).matches;

    const previousOverflow = document.body.style.overflow;
    if (isMobileSheet()) {
      document.body.style.setProperty("overflow", "hidden");
    }

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        launcherEl.focus();
        return;
      }
      if (event.key !== "Tab" || !isMobileSheet() || !panelRef.current) {
        return;
      }
      const focusable = panelRef.current.querySelectorAll(
        'button:not(:disabled), a[href], input, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      // `document.activeElement` resolves to the shadow HOST, not the
      // real focused element, whenever focus is inside a shadow tree
      // (the DOM spec's own defined behavior) -- the shadow root's OWN
      // `activeElement` is what actually reflects focus within it.
      // `getRootNode()` finds that shadow root from any element inside
      // it, with no need to thread a `shadowRoot` prop down just for this.
      const root = panelRef.current.getRootNode();
      const active = root.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.setProperty("overflow", previousOverflow || "");
    };
  }, [open, launcherEl]);

  // [ops bugfix]: the input must come back enabled AND focused after
  // EVERY way a turn can end (a normal answer, the fallback, an
  // escalation offer, a 409/503, quota exhausted, a network drop
  // mid-stream, an aborted/timed-out stream, or the visitor closing and
  // reopening the panel mid-stream) -- no spec-mandated terminal state
  // exists that should leave it dead (docs/SPEC.md §9/§11: quota-
  // exhausted still routes through this SAME `done` event with a fixed
  // fallback reply, "the escalation form still works"; §11's own state
  // list names no permanently-disabled state at all). Re-running this
  // effect on `open` too (not only `isStreaming`) is what restores focus
  // correctly even when a turn finished WHILE the panel was closed
  // (hidden elements cannot receive real focus, so that case needs a
  // second chance once the panel becomes visible again); `view` guards
  // against stealing focus onto a textarea that is not even rendered in
  // the "conversations"/"escalation" views.
  useEffect(() => {
    if (open && !isStreaming && view === "chat") {
      textareaRef.current?.focus();
    }
  }, [isStreaming, open, view]);

  const announce = (text) => {
    if (liveRegionRef.current) {
      liveRegionRef.current.textContent = text;
    }
  };

  const runTurn = (text, { isRetry }) => {
    setBanner(null);
    setIsStreaming(true);
    if (!isRetry) {
      setMessages((prev) => [
        ...prev,
        { role: "visitor", text },
        { role: "assistant", text: "", citations: [], status: "streaming" },
      ]);
    } else {
      setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, text: "", status: "streaming" })));
    }

    const callbacks = {
      // Task 5.5: the VISIBLE bubble updates on every token (no live
      // region involved at all here) -- only the SEPARATE, visually-
      // hidden region below is ever aria-live-announced, and only once
      // per completed turn, in onDone. This is what keeps a screen
      // reader from announcing every streamed token.
      onToken: (accumulated) => {
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, text: accumulated })));
      },
      onCitations: (items) => {
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, citations: items })));
      },
      onDone: (info) => {
        // Part D: a stopped turn is marked "stopped", never "done" --
        // `Message()`'s own render picks a different, distinct status
        // class/indicator from this alone.
        const status = info.stopped ? "stopped" : "done";
        setMessages((prev) => {
          const next = _updateLast(prev, "assistant", (m) => ({ ...m, status }));
          const last = next[next.length - 1];
          // Screen readers get BOTH an accessible label (the Stop
          // button's own `aria-label` below) and an accessible STATE
          // change here: the announced text itself says "(stopped)",
          // so a screen-reader user hears the difference between a
          // completed answer and one they stopped early, not just
          // silence where the rest of the answer would have been.
          announce(info.stopped ? `Assistant: ${last.text} (stopped)` : `Assistant: ${last.text}`);
          return next;
        });
        // A stopped turn never offers escalation -- it was cut short by
        // the visitor's own choice, not a fixed-reply node's verdict.
        setOfferEscalation(info.stopped ? false : Boolean(info.offerEscalation));
      },
      onError: (message) => {
        setBanner(message);
        setMessages((prev) => _updateLast(prev, "assistant", (m) => ({ ...m, status: "error" })));
      },
    };

    // [ops bugfix]: `setIsStreaming(false)` in a `finally` -- this is
    // what the old code was missing entirely for RENDERING purposes.
    // `controller.send()`/`.retry()` themselves already always resolve
    // (every internal failure path -- a non-OK response, a thrown
    // network error, a dropped connection, an SSE `error` event -- calls
    // one of `callbacks` above and returns normally; see chat-
    // controller.js's own `_sendOnce()`/`_consumeStream()`), so `.finally()`
    // here runs for every one of those cases too, not only success.
    const turnPromise = isRetry ? controller.retry(callbacks) : controller.send(text, callbacks);
    turnPromise.finally(() => setIsStreaming(false));
  };

  const handleSend = () => {
    const text = inputValue.trim();
    if (!text || isStreaming) {
      return;
    }
    setInputValue("");
    runTurn(text, { isRetry: false });
  };

  const handleRetry = () => {
    if (isStreaming) {
      return;
    }
    runTurn(null, { isRetry: true });
  };

  // Part D (docs/SPEC.md §9's "Stop mid-stream"): `controller.stop()`
  // itself resolves once the local abort has already happened
  // (chat-controller.js's own `stop()`), which is what makes `onDone`
  // fire (with `stopped: true`) and `isStreaming` flip back to `false`
  // promptly -- no extra state to manage here beyond calling it.
  const handleStop = () => {
    if (!isStreaming) {
      return;
    }
    controller.stop();
  };

  const handleOpenConversation = (cid) => {
    setView("chat");
    setBanner(null);
    controller
      .loadConversation(cid)
      .then((history) => {
        setMessages(history.map((m) => ({ role: m.role, text: m.text, citations: [], status: "done" })));
        setOfferEscalation(false);
      })
      .catch(() => setBanner("Couldn't open that chat. Please try again."));
  };

  const onInputKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  if (view === "conversations") {
    return h(
      "div",
      { className: "sf-panel", ref: panelRef, hidden: !open, role: "dialog", "aria-label": "Previous chats" },
      h(ConversationList, { controller, onOpenConversation: handleOpenConversation, onBack: () => setView("chat") }),
    );
  }

  if (view === "escalation") {
    return h(
      "div",
      { className: "sf-panel", ref: panelRef, hidden: !open, role: "dialog", "aria-label": "Talk to a person" },
      h(EscalationPanel, {
        controller,
        onClose: () => setView("chat"),
        onSubmitted: () => {
          setEscalationSubmitted(true);
          setView("chat");
        },
      }),
    );
  }

  return h(
    "div",
    { className: "sf-panel", ref: panelRef, hidden: !open, role: "dialog", "aria-label": config.title },
    h(
      "div",
      { className: "sf-header" },
      config.logoUrl ? h("img", { className: "sf-logo", src: config.logoUrl, alt: "" }) : null,
      h("span", { className: "sf-header-title" }, config.title),
      h(
        "button",
        { type: "button", className: "sf-icon-button", "aria-label": "Previous chats", onClick: () => setView("conversations") },
        "🕑",
      ),
    ),
    h(
      "div",
      { className: "sf-message-list", role: "log", "aria-live": "off", ref: listRef },
      messages.length === 0 ? h("div", { className: "sf-message sf-message-assistant" }, config.greeting) : null,
      messages.map((message, index) =>
        h(Message, {
          key: index,
          message,
          onRetry: index === messages.length - 1 ? handleRetry : null,
          verifiedDomains,
        }),
      ),
    ),
    // Task 5.5: the real announcer -- visually hidden, aria-live, text
    // set exactly once per completed turn (see runTurn's own onDone
    // above), never per token.
    h("div", { className: "sf-visually-hidden", role: "status", "aria-live": "polite", ref: liveRegionRef }),
    offerEscalation && !escalationSubmitted
      ? h("button", { type: "button", className: "sf-talk-to-human", onClick: () => setView("escalation") }, "Talk to a person")
      : null,
    escalationSubmitted ? h("p", { className: "sf-banner", role: "status" }, "Your request has been sent. Someone will be in touch.") : null,
    banner ? h("div", { className: "sf-banner", role: "status" }, banner) : null,
    h(
      "div",
      { className: "sf-input-row" },
      h("textarea", {
        ref: textareaRef,
        className: "sf-input",
        value: inputValue,
        maxLength: MAX_MESSAGE_LENGTH,
        disabled: isStreaming,
        "aria-label": "Message",
        onInput: (event) => setInputValue(event.currentTarget.value),
        onKeyDown: onInputKeyDown,
      }),
      // Part D: the SAME button slot swaps between Send and Stop on
      // `isStreaming` -- never two separate buttons swapped via
      // hidden/visible (which would leave a stale, invisible Send
      // button still in the tab order under a strict a11y audit).
      // `aria-label`/visible text/`onClick` all change together, so a
      // screen reader hears the real current action, not a stale one.
      isStreaming
        ? h(
            "button",
            {
              type: "button",
              className: "sf-send",
              "aria-label": "Stop generating",
              onClick: handleStop,
            },
            "Stop",
          )
        : h(
            "button",
            {
              type: "button",
              className: "sf-send",
              "aria-label": "Send",
              disabled: inputValue.trim() === "",
              onClick: handleSend,
            },
            "Send",
          ),
    ),
    config.privacyNoticeUrl && isHttpsUrl(config.privacyNoticeUrl)
      ? h(
          "a",
          { className: "sf-privacy-link", href: config.privacyNoticeUrl, target: "_blank", rel: "noopener noreferrer nofollow" },
          "Privacy notice",
        )
      : null,
  );
}
