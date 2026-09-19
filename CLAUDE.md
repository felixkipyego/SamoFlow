# VileSite — Embeddable Agentic AI Widget & Platform

## At the start of every session
1. Read PROJECT_SPEC.md and follow its rules. It is the living context: working rules, engineering rules, decisions, component status, the current task and open markers.
2. docs/SPEC.md is the detailed specification and the source of truth. Read only the sections a task names.
3. Tell me the current task, the next task and any open markers. Do not write code until I say so.

## When instructions conflict
1. My direct instructions in the current session come first.
2. Then PROJECT_SPEC.md (working rules, engineering rules, decisions).
3. Then docs/SPEC.md.
If PROJECT_SPEC.md and docs/SPEC.md disagree, stop and ask me which one is right.

## Keeping the files in sync
- Do not copy rules or decisions into this file. It only points to the two files above, so nothing can drift.
- After every task, update PROJECT_SPEC.md as its rules describe.
