# SamoFlow — Embeddable Agentic AI Widget & Platform

## At the start of every session
1. Read PROJECT_SPEC.md and follow its rules. It is the living context: working rules, engineering rules, decisions, component status, the current task and open markers.
2. docs/SPEC.md is the detailed specification and the source of truth. Read only the sections a task names.
3. Tell me the current task, the next task and any open markers. Do not write code until I say so.

## When instructions conflict
1. My direct instructions in the current session come first.
2. Then PROJECT_SPEC.md (working rules, engineering rules, decisions).
3. Then docs/SPEC.md.
If PROJECT_SPEC.md and docs/SPEC.md disagree, stop and ask me which one is right.

## Senior engineer judgment
Applies to every task, whether or not the prompt mentions it. Extends PROJECT_SPEC.md rule 11 (over-engineering/security) and rule 6 (the end-of-task report) — read those first; this does not repeat them.
- State the simplest correct design before building, and justify every piece of complexity beyond it.
- Scale rigor to risk: [SECURITY] work gets full proof (live tests, non-vacuous checks, failure modes); plumbing and refactors stay light.
- Challenge the prompt, not just the design — a request that is wrong, unsafe, over-built or under-built gets said so before building, not built faithfully.
- Think in production terms: dependency failure/slowness, cost, how it's observed and debugged, how it's rolled back, whether persisted data stays readable across library upgrades.
- Never widen a security guard's allow-list for convenience — fix the design instead.
- [SECURITY] work includes an adversarial pass before reporting: actively try to break what you built as an attacker would (bypasses, splits, nesting, encodings), and convert every finding into a committed regression test.
- Every report ends with a "Senior engineer check": (1) what you chose NOT to build and why; (2) the main trade-off accepted and its later cost; (3) anything in the prompt you pushed back on or would've done differently; (4) the highest production risk in this change. "None" only with a one-line reason. A report without this section is incomplete.

## Ending a task
Applies to every task.
- When the work is finished and verified, write the final report and end — the report is the last thing you produce.
- Never ask whether to stop, continue, or proceed; never close with a question, an offer ("Want me to...?", "Shall I...?", "Let me know if..."), or a list of options; never use an interactive question tool at the end of a task.
- Anything needing a decision goes in a "Decisions needed" section inside the report, as statements with your recommendation — not as questions.
- Never start the next task, step, or subtask unless the prompt explicitly says to.
- Exception: if genuinely blocked mid-task (cannot continue safely or correctly without input), stop there and report the blocker in the same report format — never ask permission for things the prompt already authorised.

## Keeping the files in sync
- Do not copy rules or decisions into this file, with one exception: "Senior engineer judgment" and "Ending a task" above are standing, project-independent rules, not SamoFlow spec decisions, so they live here directly rather than in PROJECT_SPEC.md. Everything else still only points to the two files above, so nothing can drift.
- After every task, update PROJECT_SPEC.md as its rules describe.
