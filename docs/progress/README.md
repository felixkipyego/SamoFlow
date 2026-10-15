# docs/progress/

Per-track progress and decision logs, for the parallel-worktree protocol
ratified 2026-10-15 (PROJECT_SPEC.md §11.5 and §5's own "Parallel-track
protocol" decision-log entry).

While `track/widget` and `track/dashboard` are both in flight, each one's
own progress and decisions live here (`widget.md`, `dashboard.md`) instead
of `PROJECT_SPEC.md`'s own §4/§5/§7 — the one shared file both tracks
would otherwise need to edit on every sub-step, and the one real source of
cross-track merge conflicts this protocol exists to avoid.

Each track creates its own `<track>.md` the first time it has something
to record, following the same shape PROJECT_SPEC.md's own §5 decision log
already uses (dated entries: what was built, what was verified).

When a track merges, its own `docs/progress/<track>.md` content is folded
into PROJECT_SPEC.md's real sections in the merge commit itself, and the
file is removed — this directory is a staging area for the two tracks'
own history, not a permanent second home for it.
