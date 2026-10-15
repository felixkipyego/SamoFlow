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

// Not run through esbuild: one small, static stylesheet (no preprocessor,
// no variables yet -- design tokens/theming are Step 5.5's own concern),
// so a plain copy into dist/ is all that's needed (rule 11: no build
// pipeline step this file doesn't yet require).
await mkdir(path.join(widgetRoot, "dist"), { recursive: true });
await copyFile(
  path.join(widgetRoot, "loader/src/widget.css"),
  path.join(widgetRoot, "dist/widget.css"),
);

console.log("built dist/widget.js and dist/widget.css");
