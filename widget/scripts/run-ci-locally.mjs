// Task 5.1 Part A: runs .github/workflows/widget.yml's own "widget" job
// steps, in the SAME ORDER, with the SAME working directory, so a
// local/CI ordering mismatch (the real root cause of CI run #1/#2 both
// failing at "Unit tests" -- size-budget.test.mjs needs dist/widget.js,
// which only the Build step produces, and Build was ordered AFTER Unit
// tests) is caught before pushing, not after.
//
// Deliberately reads the real widget.yml and extracts its `run:` lines,
// rather than a hand-maintained second copy of the step list: a
// hand-maintained mirror is exactly what would drift out of sync again
// the next time someone reorders a step in the real file without
// remembering to also update a parallel copy here. No YAML parser (no
// new dependency) -- line-based extraction only, matching this repo's
// own backend/tests/test_ci_guard.py convention for the same reason.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const widgetRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const repoRoot = path.resolve(widgetRoot, "..");
const workflowPath = path.join(repoRoot, ".github/workflows/widget.yml");

function extractJobRunCommands(yamlText, jobName) {
  const lines = yamlText.split("\n");
  const jobHeaderPattern = new RegExp(`^ {2}${jobName}:\\s*$`);
  const nextJobOrStepsPattern = /^ {2}\w[\w-]*:\s*$/;
  const stepNamePattern = /^\s{6}- name:\s*(.+)$/;
  const runLinePattern = /^\s*run:\s*(.+)$/;

  const start = lines.findIndex((line) => jobHeaderPattern.test(line));
  if (start === -1) {
    throw new Error(`no job named "${jobName}" found in ${workflowPath}`);
  }

  const jobLines = [];
  for (const line of lines.slice(start + 1)) {
    if (nextJobOrStepsPattern.test(line)) {
      break;
    }
    jobLines.push(line);
  }

  const steps = [];
  let currentName = null;
  for (const line of jobLines) {
    const nameMatch = stepNamePattern.exec(line);
    if (nameMatch) {
      currentName = nameMatch[1].trim();
      continue;
    }
    const runMatch = runLinePattern.exec(line);
    if (runMatch && currentName) {
      steps.push({ name: currentName, command: runMatch[1].trim() });
      currentName = null;
    }
  }
  return steps;
}

const workflowText = readFileSync(workflowPath, "utf8");
const steps = extractJobRunCommands(workflowText, "widget");

if (steps.length === 0) {
  console.error(`no 'run:' steps found for the widget job in ${workflowPath}`);
  process.exit(1);
}

console.log(`Running ${steps.length} step(s) from ${path.relative(repoRoot, workflowPath)}, in order, cwd=${widgetRoot}:\n`);

for (const step of steps) {
  console.log(`--- ${step.name} (${step.command}) ---`);
  const result = spawnSync(step.command, {
    cwd: widgetRoot,
    shell: true,
    stdio: "inherit",
  });
  if (result.status !== 0) {
    console.error(`\nFAILED at step "${step.name}" (${step.command}), exit code ${result.status}`);
    process.exit(result.status ?? 1);
  }
}

console.log("\nAll widget.yml steps passed, in the real CI order.");
