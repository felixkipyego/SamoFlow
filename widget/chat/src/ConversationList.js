// Task 5.3: "Previous chats" (docs/SPEC.md §10/§11 -- GET /conversations,
// opening one via GET /conversations/{cid}/messages). Handles the
// empty, loading and error states explicitly, per this step's own
// instruction -- none of the three is "just don't show anything".
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";

export function ConversationList({ controller, onOpenConversation, onBack }) {
  const [state, setState] = useState({ status: "loading", conversations: [] });

  const load = () => {
    setState({ status: "loading", conversations: [] });
    controller
      .listConversations()
      .then((conversations) => setState({ status: "loaded", conversations }))
      .catch(() => setState({ status: "error", conversations: [] }));
  };

  useEffect(load, [controller]);

  return h(
    "div",
    { className: "sf-conversation-wrapper" },
    h(
      "div",
      { className: "sf-header" },
      h("button", { type: "button", className: "sf-icon-button", "aria-label": "Back to chat", onClick: onBack }, "←"),
      h("span", { className: "sf-header-title" }, "Previous chats"),
    ),
    state.status === "loading"
      ? h("p", { className: "sf-loading-state", role: "status" }, "Loading your previous chats…")
      : null,
    state.status === "error"
      ? h(
          "div",
          { className: "sf-error-state", role: "alert" },
          h("p", null, "Couldn't load your previous chats."),
          h("button", { type: "button", className: "sf-retry", onClick: load }, "Retry"),
        )
      : null,
    state.status === "loaded" && state.conversations.length === 0
      ? h("p", { className: "sf-empty-state" }, "You haven't started a chat yet.")
      : null,
    state.status === "loaded" && state.conversations.length > 0
      ? h(
          "ul",
          { className: "sf-conversation-list" },
          state.conversations.map((conversation) =>
            h(
              "li",
              { key: conversation.conversation_id },
              h(
                "button",
                {
                  type: "button",
                  className: "sf-conversation-item",
                  onClick: () => onOpenConversation(conversation.conversation_id),
                },
                conversation.title || "Untitled chat",
              ),
            ),
          ),
        )
      : null,
  );
}
