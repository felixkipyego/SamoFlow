// Task 5.1: the same budget check scripts/check-size.mjs runs for CI's
// "size budget" step, also wired into `npm test` so a regression is
// caught locally without needing to remember the separate `npm run size`
// step. Requires `npm run build` to have produced dist/widget.js first
// (true for both CI and this repo's own widget.yml ordering).
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CHAT_UI_BUDGET_BYTES,
  LOADER_BUDGET_BYTES,
  checkBudget,
} from "../../scripts/budgets.mjs";

const widgetRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);

test("the built loader bundle (dist/widget.js) stays within its ~4KB gzipped budget", () => {
  const result = checkBudget(
    path.join(widgetRoot, "dist/widget.js"),
    LOADER_BUDGET_BYTES,
    "loader",
  );
  assert.notEqual(
    result.status,
    "missing",
    "dist/widget.js was not found -- run `npm run build` before `npm test`",
  );
  assert.equal(result.status, "ok", result.message);
});

test("placeholder: the chat UI bundle's ~60KB gzipped budget, owned by Step 5.2", (t) => {
  const result = checkBudget(
    path.join(widgetRoot, "chat/dist/chat.js"),
    CHAT_UI_BUDGET_BYTES,
    "chat UI",
  );
  if (result.status === "missing") {
    t.skip(result.message);
    return;
  }
  assert.equal(result.status, "ok", result.message);
});
