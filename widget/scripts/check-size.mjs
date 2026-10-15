// Task 5.1: CI's "size budget" step (`npm run size`). Run after
// `npm run build`, which produces dist/widget.js.
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CHAT_UI_BUDGET_BYTES,
  LOADER_BUDGET_BYTES,
  checkBudget,
} from "./budgets.mjs";

const widgetRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

const results = [
  checkBudget(
    path.join(widgetRoot, "dist/widget.js"),
    LOADER_BUDGET_BYTES,
    "loader (dist/widget.js)",
  ),
  checkBudget(
    path.join(widgetRoot, "chat/dist/chat.js"),
    CHAT_UI_BUDGET_BYTES,
    "chat UI (chat/dist/chat.js)",
  ),
];

let failed = false;
for (const result of results) {
  console.log(result.message);
  if (result.status === "over") {
    failed = true;
  }
}

process.exit(failed ? 1 : 0);
