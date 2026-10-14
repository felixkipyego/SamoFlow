# backend/app/evals/eval_summary.py
# Task 3.7.e, item 3: shared by all three eval entry points (`make eval`,
# `make eval-answers`, `make eval-calibrate`) -- each writes a small,
# COMMITTED (never gitignored), metrics-only summary file and prints a
# delta table against whatever was PREVIOUSLY committed at that same path,
# so a regression shows up both in the terminal (immediately) and in git
# (the next `git diff`/PR review). Lives under app/evals/ (not scripts/):
# pure dict/JSON manipulation, no Qdrant/Settings/provider-SDK import --
# confirmed safe against both the Qdrant read-path guard and the
# chat-model guard (both scan app/ specifically, and both read nothing
# but stdlib json/pathlib here).
import json
from pathlib import Path


def load_previous_summary(path: Path) -> dict | None:
    """`None` means "no committed summary yet" (the first run at this
    path) -- distinct from an empty dict, which would wrongly make every
    metric look like a brand-new one in the delta table below.
    """
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _flatten(prefix: str, value: object) -> dict[str, object]:
    if isinstance(value, dict):
        flat: dict[str, object] = {}
        for key, sub_value in value.items():
            flat.update(_flatten(f"{prefix}.{key}" if prefix else str(key), sub_value))
        return flat
    return {prefix: value}


def flatten_summary(summary: dict) -> dict[str, object]:
    """Dotted-path leaf values only (e.g. "recall_at_k.answerable.1" ->
    0.93) -- every summary dict in this project is a plain nested
    dict-of-dicts of numbers/strings/None, confirmed by reading each of
    the three callers directly, so no list/tuple case is handled here.
    """
    return _flatten("", summary)


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def format_delta_table(previous: dict | None, current: dict) -> str:
    """One row per metric key in `current` (dotted path, sorted) plus any
    key `previous` had that `current` no longer does (a removed metric,
    reported rather than silently dropped). `previous=None` (no prior
    committed summary) prints "(first run)" for every delta rather than
    a misleading 0 or a crash on `.get()`.
    """
    previous_flat = flatten_summary(previous) if previous is not None else {}
    current_flat = flatten_summary(current)

    header = f"{'metric':<52} {'previous':>12} {'current':>12} {'delta':>12}"
    lines = [header, "-" * len(header)]

    for key in sorted(current_flat):
        new_value = current_flat[key]
        has_previous = previous is not None and key in previous_flat
        old_value = previous_flat.get(key)
        numeric = (
            isinstance(new_value, (int, float))
            and not isinstance(new_value, bool)
            and isinstance(old_value, (int, float))
            and not isinstance(old_value, bool)
        )
        if previous is None:
            delta_str = "(first run)"
        elif not has_previous:
            delta_str = "(new metric)"
        elif numeric:
            delta_str = f"{new_value - old_value:+.4f}"
        else:
            delta_str = "n/a"
        old_str = "-" if not has_previous else _format_value(old_value)
        lines.append(f"{key:<52} {old_str:>12} {_format_value(new_value):>12} {delta_str:>12}")

    if previous is not None:
        for key in sorted(set(previous_flat) - set(current_flat)):
            lines.append(
                f"{key:<52} {_format_value(previous_flat[key]):>12} {'(removed)':>12} {'n/a':>12}"
            )

    return "\n".join(lines)
