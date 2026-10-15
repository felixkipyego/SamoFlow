# widget/chat

The Preact chat UI that runs inside the widget's shadow DOM: session
creation, streaming answers via `fetch`+SSE, a restricted safe-markdown
renderer, and the calm fixed error states (docs/SPEC.md §11). Previous
chats, the escalation panel and the full theming/accessibility pass are
still ahead (steps 5.3–5.5).

Built at Step 5.2 (Chat UI core [SECURITY]) — see PROJECT_SPEC.md and
`docs/progress/widget.md`. Source in `src/`, unit tests in `test/`.
Bundled with esbuild (`../scripts/build.mjs`) from the repo's `widget/`
package (`../package.json`).
