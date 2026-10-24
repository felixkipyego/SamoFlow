# backend/tests/test_grype_guard.py
# Regression guard for .grype.yaml's ignore rules, so a future edit cannot
# silently widen them (e.g. dropping a field, so a rule starts matching a
# different package/version's CVE by coincidence) or add an unreviewed one.
#
# 2026-10-24 (Felix's decision, D-pillow-audit-ignores): the ONLY approved
# ignores are pillow 12.3.0 findings that map to an ID in the Makefile's
# PILLOW_AUDIT_IGNORES (the pip-audit list) -- every rule must be scoped to
# exactly pillow / 12.3.0 / python, and its `reason` must name the approved
# ID it maps to (Grype keys these advisories by GHSA ID and, confirmed live
# on grype v0.119.0, does not match a rule written with the related CVE).
# Uses PyYAML (already a runtime dependency, app/agent/escalation_phrases.py)
# instead of the earlier hand-rolled line parser, which could only check
# that fields existed, not what they said.
import re

import yaml

from tests.conftest import REPO_ROOT

GRYPE_YAML_PATH = REPO_ROOT / ".grype.yaml"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

APPROVED_PACKAGE = {"name": "pillow", "version": "12.3.0", "type": "python"}
# Approved pip-audit IDs that Grype's own database does not carry at all
# (confirmed by a full unfiltered scan of the built image, 2026-10-24), so
# they legitimately have no .grype.yaml rule. Anything else in the Makefile
# list must have one.
APPROVED_IDS_GRYPE_DOES_NOT_REPORT = {"CVE-2026-112398", "CVE-2026-114501"}


def _rules() -> list[dict]:
    return yaml.safe_load(GRYPE_YAML_PATH.read_text())["ignore"] or []


def _approved_ids() -> set[str]:
    makefile = MAKEFILE_PATH.read_text()
    block = re.search(r"^PILLOW_AUDIT_IGNORES = \\\n((?:\t\S+(?: \\)?\n)+)", makefile, re.M)
    assert block, "Makefile no longer defines PILLOW_AUDIT_IGNORES in the expected shape"
    return set(re.findall(r"\t(\S+)", block.group(1)))


def test_approved_list_is_exactly_the_41_pillow_ids():
    # 37 from pip-audit + 4 Grype-only, approved 2026-10-24
    # (D-pillow-grype-unapproved).
    ids = _approved_ids()
    assert len(ids) == 41
    assert {"CVE-2026-112394", "CVE-2026-112403", "CVE-2026-112898", "CVE-2026-112902"} <= ids
    assert all(re.fullmatch(r"(CVE-\d{4}-\d+|GHSA(-[a-z0-9]{4}){3})", i) for i in ids)


def test_audit_target_ignores_by_id_from_that_list_only():
    makefile = MAKEFILE_PATH.read_text()
    audit = re.search(r"^audit:\n\t(.+)$", makefile, re.M).group(1)
    assert "$(foreach id,$(PILLOW_AUDIT_IGNORES),--ignore-vuln $(id))" in audit
    # No other ignore mechanism, and no severity/skip softening.
    assert audit.count("--ignore-vuln") == 1
    for forbidden in ("--skip-editable", "--ignore-vuln CVE", "--ignore-vuln GHSA", "|| true"):
        assert forbidden not in audit


def _violations(rules: list[dict], approved: set[str]) -> list[str]:
    # Every reason a rule set is NOT the approved exception. Shared by the
    # real-file test and the widening cases below, so both run one check.
    found = []
    vulns = [rule.get("vulnerability") for rule in rules]
    if len(vulns) != len(set(vulns)):
        found.append("duplicate .grype.yaml rule")
    mapped = []
    for rule in rules:
        if set(rule) != {"vulnerability", "package", "reason"}:
            found.append(f"{rule.get('vulnerability')}: fields {sorted(rule)}")
            continue
        if rule["package"] != APPROVED_PACKAGE:
            found.append(f"{rule['vulnerability']}: not scoped to pillow/12.3.0/python")
        approved_id = str(rule["reason"]).split(" ", 1)[0]
        if approved_id not in approved:
            found.append(
                f"{rule['vulnerability']}: reason names {approved_id!r}, not in the "
                "Makefile's PILLOW_AUDIT_IGNORES -- a new pillow finding needs "
                "Felix's approval first, never a silent .grype.yaml rule"
            )
        if not (rule["vulnerability"] == approved_id or rule["vulnerability"].startswith("GHSA-")):
            found.append(f"{rule['vulnerability']}: not a GHSA ID or the approved ID itself")
        mapped.append(approved_id)
    if len(mapped) != len(set(mapped)):
        found.append("two rules claim the same approved ID")
    return found


def test_every_rule_is_the_approved_scoped_pillow_exception():
    assert _violations(_rules(), _approved_ids()) == []


def test_the_two_lists_cannot_drift():
    covered = {rule["reason"].split(" ", 1)[0] for rule in _rules()}
    missing = _approved_ids() - covered - APPROVED_IDS_GRYPE_DOES_NOT_REPORT
    assert not missing, f"approved IDs with no .grype.yaml rule: {sorted(missing)}"
    assert APPROVED_IDS_GRYPE_DOES_NOT_REPORT <= _approved_ids()
    assert not (APPROVED_IDS_GRYPE_DOES_NOT_REPORT & covered)


def test_each_kind_of_widening_is_rejected():
    # Non-vacuous: the real check above rejects every way a rule could widen.
    good = _rules()[0]
    widened = {
        "another version": {**good, "package": {**APPROVED_PACKAGE, "version": "13.0.0"}},
        "no type field": {**good, "package": {"name": "pillow", "version": "12.3.0"}},
        "another package": {**good, "package": {**APPROVED_PACKAGE, "name": "pypdf"}},
        "unapproved id": {**good, "reason": "CVE-2026-999999 -- not approved"},
        "no reason": {k: v for k, v in good.items() if k != "reason"},
        "no package": {k: v for k, v in good.items() if k != "package"},
    }
    for label, rule in widened.items():
        assert _violations([rule], _approved_ids()), label
    assert _violations([good, good], _approved_ids()), "duplicate"
