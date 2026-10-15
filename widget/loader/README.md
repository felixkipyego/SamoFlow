# widget/loader

The widget's loader script (`widget.js`): reads `data-site-key` and
`data-base-url` from its own `<script>` tag, mounts an open shadow root with
an external stylesheet (CSP-safe), and lazily loads the chat UI bundle on
first launcher click (docs/SPEC.md §11).

Built at Step 5.1 (Loader and shadow DOM shell) — see PROJECT_SPEC.md and
`docs/progress/widget.md`. Source in `src/`, unit tests in `test/`. Bundled
with esbuild (`../scripts/build.mjs`, kickoff decision #1) from the repo's
`widget/` package (`../package.json`).
