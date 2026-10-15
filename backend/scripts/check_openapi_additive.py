# backend/scripts/check_openapi_additive.py
# Step 6.1: this task's own direct instruction -- "API changes are
# ADDITIVE ONLY: new paths and schemas. Any change to an existing path...
# is stop condition (a). Regenerate docs/openapi.json and prove the diff
# only adds, with a script that compares old and new."
#
# Scope, stated explicitly: "additive only" is checked against `paths`
# and `components.schemas` -- the two subtrees that actually define the
# wire contract either track's own frontend code depends on
# (PROJECT_SPEC.md §11.5's own "the API contract is locked" rule). Other
# top-level keys (`info.version`, generated purely from this project's
# own package version) are allowed to change freely; they carry no
# compatibility meaning for a client.
#
# Rule, applied recursively: every key present in the OLD schema's
# `paths`/`components.schemas` must still be present in the NEW schema,
# with an IDENTICAL value (deep equality) for that key -- removing or
# changing something the old schema already had is flagged, matching
# this task's own "any change to an existing path... is stop condition
# (a)". New keys may be added anywhere, including a new, additional
# property on an already-existing schema (the standard, backward-
# compatible way an OpenAPI shape evolves -- an old client that never
# reads the new field is unaffected). The simplest correct check for
# "did anything that already existed get removed or change", not an
# attempt to classify every kind of change by real-world client impact.
import json
import sys
from pathlib import Path


class NotAdditiveError(Exception):
    pass


def _check_subset_recursive(old: object, new: object, path: str) -> list[str]:
    violations = []
    if isinstance(old, dict):
        if not isinstance(new, dict):
            return [f"{path}: was an object, is now {type(new).__name__}"]
        for key, old_value in old.items():
            if key not in new:
                violations.append(f"{path}/{key}: removed")
                continue
            violations.extend(_check_subset_recursive(old_value, new[key], f"{path}/{key}"))
    elif old != new:
        violations.append(f"{path}: changed from {old!r} to {new!r}")
    return violations


def check_additive(old_schema: dict, new_schema: dict) -> list[str]:
    violations = []
    violations.extend(
        _check_subset_recursive(old_schema.get("paths", {}), new_schema.get("paths", {}), "paths")
    )
    old_schemas = old_schema.get("components", {}).get("schemas", {})
    new_schemas = new_schema.get("components", {}).get("schemas", {})
    violations.extend(_check_subset_recursive(old_schemas, new_schemas, "components/schemas"))
    return violations


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: check_openapi_additive.py <old.json> <new.json>", file=sys.stderr)
        sys.exit(2)
    old_schema = json.loads(Path(sys.argv[1]).read_text())
    new_schema = json.loads(Path(sys.argv[2]).read_text())
    violations = check_additive(old_schema, new_schema)
    if violations:
        print("NOT additive-only -- the following existing paths/schemas changed:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        sys.exit(1)
    old_paths = set(old_schema.get("paths", {}))
    new_paths = set(new_schema.get("paths", {}))
    print(f"additive-only: confirmed. {len(new_paths - old_paths)} new path(s) added:")
    for path in sorted(new_paths - old_paths):
        print(f"  + {path}")


if __name__ == "__main__":
    main()
