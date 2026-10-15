// Task 5.1: builds the loader bundle with esbuild (kickoff decision #1).
// Classic-script IIFE output, no sourcemap shipped by default -- see
// loader/src/widget.js's own header for why this must stay a classic
// script (document.currentScript).
import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const widgetRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

await build({
  entryPoints: [path.join(widgetRoot, "loader/src/widget.js")],
  outfile: path.join(widgetRoot, "dist/widget.js"),
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2022"],
  legalComments: "none",
  sourcemap: false,
  logLevel: "info",
});

// Task 5.2: the chat UI bundle, same classic-script IIFE shape as the
// loader and for the same reason (chat/src/index.js's own
// document.currentScript use). Plain h()-call components throughout,
// deliberately -- no JSX syntax anywhere under chat/src/, so no esbuild
// JSX loader configuration is needed here at all.
await build({
  entryPoints: [path.join(widgetRoot, "chat/src/index.js")],
  outfile: path.join(widgetRoot, "chat/dist/chat.js"),
  bundle: true,
  minify: true,
  format: "iife",
  target: ["es2022"],
  legalComments: "none",
  sourcemap: false,
  logLevel: "info",
});

// Not run through esbuild: two small, static stylesheets (no
// preprocessor, no variables yet -- design tokens/theming are Step
// 5.5's own concern), so a plain copy into dist/ is all that's needed
// (rule 11: no build pipeline step this file doesn't yet require).
await mkdir(path.join(widgetRoot, "dist"), { recursive: true });
await copyFile(
  path.join(widgetRoot, "loader/src/widget.css"),
  path.join(widgetRoot, "dist/widget.css"),
);
await mkdir(path.join(widgetRoot, "chat/dist"), { recursive: true });
await copyFile(
  path.join(widgetRoot, "chat/src/chat.css"),
  path.join(widgetRoot, "chat/dist/chat.css"),
);

console.log("built dist/widget.{js,css} and chat/dist/chat.{js,css}");
