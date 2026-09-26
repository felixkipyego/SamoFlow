"""Lists TODO/ASSUMPTION/UNCERTAIN/NOTE markers in the code (Step 1.1c;
see PROJECT_SPEC.md §3, "make hooks").

A visibility tool, not a gate: it never fails because markers exist (that
is the whole point of a marker -- work still to do, verify, or resolve).
It only exits non-zero if the scan itself errors (e.g. a file it can't
read), per PROJECT_SPEC.md's own instruction for this task.

Plain text matching, no AST parsing, no new dependency: walks every .py
file under the given root directories (backend/, evals/ by default),
skipping the same kind of directories ruff's own defaults exclude
(virtualenvs, __pycache__, pytest/ruff caches). TODO markers are grouped
by their PROJECT_SPEC.md owning step, parsed from the "TODO(<step>)"
convention (rule 4); ASSUMPTION/UNCERTAIN/NOTE carry no step number (same
rule), so they are listed under their own marker-name section instead.
"""

import re
import sys
from pathlib import Path

_SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache"}

_MARKER_LINE = re.compile(r"\b(TODO|ASSUMPTION|UNCERTAIN|NOTE)\b")
_TODO_STEP = re.compile(r"TODO\(([^)]+)\)")

DEFAULT_ROOTS = ("backend", "evals")


def _is_skipped(path: Path) -> bool:
    return any(part in _SKIP_DIR_NAMES or part.startswith(".venv") for part in path.parts)


def _step_sort_key(step: str):
    parts = step.split(".")
    try:
        return (0, tuple(int(p) for p in parts))
    except ValueError:
        return (1, step)


def find_markers(roots: list[Path]) -> list[tuple[str, Path, int, str]]:
    """Returns (marker, path, line_number, line_text) for every match."""
    found = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if _is_skipped(path):
                continue
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                match = _MARKER_LINE.search(line)
                if match:
                    found.append((match.group(1), path, line_number, line.strip()))
    return found


def format_report(markers: list[tuple[str, Path, int, str]]) -> str:
    todo_by_step: dict[str, list[tuple[Path, int, str]]] = {}
    other_by_marker: dict[str, list[tuple[Path, int, str]]] = {}

    for marker, path, line_number, line in markers:
        if marker == "TODO":
            step_match = _TODO_STEP.search(line)
            step = step_match.group(1) if step_match else "(unlabelled)"
            todo_by_step.setdefault(step, []).append((path, line_number, line))
        else:
            other_by_marker.setdefault(marker, []).append((path, line_number, line))

    lines = [f"{len(markers)} marker(s) found."]

    for step in sorted(todo_by_step, key=_step_sort_key):
        lines.append(f"\n=== TODO: Step {step} ===")
        for path, line_number, line in sorted(todo_by_step[step]):
            lines.append(f"  {path}:{line_number}  {line}")

    for marker in sorted(other_by_marker):
        lines.append(f"\n=== {marker} ===")
        for path, line_number, line in sorted(other_by_marker[marker]):
            lines.append(f"  {path}:{line_number}  {line}")

    return "\n".join(lines)


def main() -> int:
    roots = [Path(root) for root in DEFAULT_ROOTS]
    for root in roots:
        if not root.is_dir():
            print(f"hooks.py: root directory not found: {root}", file=sys.stderr)
            return 1

    try:
        markers = find_markers(roots)
    except OSError as exc:
        print(f"hooks.py: could not read a file during the scan: {exc}", file=sys.stderr)
        return 1

    print(format_report(markers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
