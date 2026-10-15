// Task 5.1: the widget's size budgets (docs/SPEC.md §11/§17), as plain
// configuration -- never hard-coded separately at each call site. Used by
// both scripts/check-size.mjs (the CI "size budget" step) and
// loader/test/size-budget.test.mjs (so `npm test` catches a regression
// locally too, from the same numbers).
import { gzipSync } from "node:zlib";
import { readFileSync } from "node:fs";

// docs/SPEC.md §11/§17: "Loader about 4KB gzipped". A real, enforced
// budget -- Step 5.1 builds the real loader bundle this is measured
// against.
export const LOADER_BUDGET_BYTES = 4096;

// docs/SPEC.md §11/§17: "chat UI about 60KB gzipped". Owned by Step 5.2,
// which has not built chat/dist/chat.js yet -- see checkBudget's
// "missing" result below for how this stays a placeholder, not a
// fabricated check against a file that doesn't exist.
export const CHAT_UI_BUDGET_BYTES = 61440;

export function gzipSize(filePath) {
  return gzipSync(readFileSync(filePath)).length;
}

/**
 * @param {string} filePath
 * @param {number} budgetBytes
 * @param {string} label
 * @returns {{status: "ok" | "over" | "missing", size?: number, message: string}}
 */
export function checkBudget(filePath, budgetBytes, label) {
  let size;
  try {
    size = gzipSize(filePath);
  } catch (err) {
    if (err.code === "ENOENT") {
      return {
        status: "missing",
        message: `${label}: not built yet (${filePath}) -- budget is ${budgetBytes} bytes gzipped, enforced once that bundle exists`,
      };
    }
    throw err;
  }

  if (size > budgetBytes) {
    return {
      status: "over",
      size,
      message: `${label}: ${size} bytes gzipped EXCEEDS its ${budgetBytes}-byte budget (docs/SPEC.md §17)`,
    };
  }

  return {
    status: "ok",
    size,
    message: `${label}: ${size} bytes gzipped (budget ${budgetBytes})`,
  };
}
