# backend/tests/test_hooks.py
# Step 1.1c: hooks.py lists TODO/ASSUMPTION/UNCERTAIN/NOTE markers (see
# PROJECT_SPEC.md §3, "make hooks"). Run as a subprocess with cwd=REPO_ROOT
# (unlike evals/run.py, hooks.py's own correctness depends on the
# repo-root-relative backend/ and evals/ paths it scans) and a restricted
# environment (tests.conftest.minimal_subprocess_env(), shared with
# test_evals_placeholder.py), so a future real implementation cannot pass
# this suite by accident while still depending on ambient state.
import subprocess
import sys

from tests.conftest import REPO_ROOT, minimal_subprocess_env

_HOOKS_PY = REPO_ROOT / "hooks.py"


def test_hooks_py_exists():
    assert _HOOKS_PY.is_file(), (
        f"hooks.py not found at {_HOOKS_PY}. If it moved, update the path "
        "derivation in this test (hooks.py relative to REPO_ROOT)."
    )


def test_hooks_py_runs_and_finds_known_markers():
    result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
        [sys.executable, str(_HOOKS_PY)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=REPO_ROOT,
        env=minimal_subprocess_env(),
    )

    assert result.returncode == 0, (
        f"hooks.py must never fail because markers exist -- a visibility "
        f"tool, not a gate. stderr: {result.stderr!r}"
    )
    assert result.stderr == ""

    first_line = result.stdout.splitlines()[0]
    count = int(first_line.split()[0])
    assert count > 0, f"expected at least one marker, got: {first_line!r}"

    # Two markers known to already exist in the codebase (not the full list,
    # so this test does not become brittle as markers are added/resolved).
    assert "worker.py" in result.stdout and "TODO(2.1)" in result.stdout
    assert "alembic/env.py" in result.stdout and "TODO(1.2)" in result.stdout


def test_hooks_py_exits_cleanly_on_an_undecodable_file(tmp_path):
    # A scratch tree, never the real backend/evals: a root directory named
    # "backend" and "evals" (so main()'s own root-directory check passes)
    # containing one non-UTF-8 .py file, proving hooks.py's documented
    # failure mode (a file it can't read) without an uncaught traceback.
    (tmp_path / "backend").mkdir()
    (tmp_path / "evals").mkdir()
    (tmp_path / "backend" / "bad.py").write_bytes(b"\xff\xfe\x00\x01")

    result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
        [sys.executable, str(_HOOKS_PY)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tmp_path,
        env=minimal_subprocess_env(),
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "hooks.py: could not read a file during the scan:" in result.stderr


def _run_hooks(cwd) -> str:
    result = subprocess.run(  # noqa: S603 (fixed args: sys.executable + a path, not user input)
        [sys.executable, str(_HOOKS_PY)],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
        env=minimal_subprocess_env(),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_real_repository_marker_count_is_64():
    # The real, live regression this task's own exclusion exists for:
    # Task 3.7.d's own injection-detector constant and its test both
    # mirror real planted-injection text verbatim, which happens to
    # contain one of this scanner's own four trigger words as an
    # ordinary English word, not a real marker comment -- confirmed by
    # reading both lines directly before this task's own fix. Pinned
    # here as a real number, not just "fewer than before". (Deliberately
    # avoids spelling any of the four trigger words literally anywhere in
    # THIS file's own comments/strings -- this file is itself scanned,
    # and is not in the exclusion set below.)
    #
    # 71 -> 70 at Task 4.3 (2026-10-14), real, not accidental: app/
    # notifications/service.py's own Step-1.1b-era deferral marker,
    # resolved -- the real send_email() replaced that whole stub.
    # Confirmed by diffing `python hooks.py` output directly: exactly
    # one real removal, plus two unrelated lines in app/worker.py that
    # only shifted line numbers (new code added above them in this same
    # task), not a second/third removal.
    #
    # 72 -> 71 at Task 4.3 (2026-10-14), real, not accidental: app/chat/
    # routes.py's own quota-related marker, resolved -- the quota system
    # is real now, replacing that comment with a real description of the
    # check order rather than a deferral note. Confirmed by diffing
    # `python hooks.py` output directly (no other marker moved).
    #
    # 76 -> 72 at Task 4.3 (2026-10-14), real, not accidental: diffed
    # `python hooks.py` output before/after directly (not just trusted
    # the new total) and confirmed exactly 4 real removals, 0 real
    # additions: app/agent/graph.py's own Step-4.1-era quota-related
    # marker (resolved -- `_quota_exhausted()` now reads a real
    # pre-computed verdict out of config); and all three of the
    # Step-1.1b-era `app/plans/service.py` stub's own markers (one of
    # each of the first three trigger words, gone together, since that
    # whole stub file was replaced by the real `get_plan_limits()`). Two
    # OTHER lines shifted in the diff (app/agent/graph.py's own
    # escalation-related marker, and its own cross-reference to this
    # scanner by name) -- confirmed these are the SAME two marker
    # instances at new line numbers (code was added above them in this
    # same file), not a 5th/6th removal-and-re-add pair.
    #
    # 75 -> 76 at Task 4.2 (2026-10-14), real, not accidental: app/chat/
    # schemas.py's two new response shapes (GET /conversations, GET
    # /conversations/{cid}/messages) carry the identical wire-naming-
    # freedom marker SessionResponse already had (app/auth/schemas.py) --
    # a genuinely new instance of an already-established marker, not a
    # duplicate of the same comment. Confirmed by diffing this task's own
    # changed files against the four trigger words directly before
    # re-pinning this number (no other marker moved).
    #
    # 77 -> 75 at Task 3.7.f (2026-10-14), real, not accidental: rule 10
    # ("verify then remove" a resolved marker) applied to
    # `gate_relevance_threshold`'s own provisional-default marker
    # (app/config.py) once real golden-set evidence (3.7.c's gate sweep)
    # let Felix ratify 0.20 -- the field's own comment AND its mirrored
    # one-line reference in app/agent/graph.py's `_gate_node` docstring
    # both lost that word, confirmed by grepping both files directly
    # before pinning this new number (no other marker moved).
    # 70 -> 68 at Step 4.4 (2026-10-15), real, not accidental: confirmed
    # by grepping directly (not just trusting the new total) that this
    # scanner's own first trigger word, followed by "(4.4)", no longer
    # appears anywhere under app/ -- exactly two real removals, both
    # resolved by this step's own real code: app/handoff/routes.py's
    # own Step-1.1b-era stub marker (the real submit_escalation()
    # endpoint replaced that whole stub), and app/agent/graph.py's own
    # `_is_escalation_intent()` deferral marker (the real YAML trigger-
    # phrase check replaced it). No other marker moved.
    # 68 -> 67 at Step 4.5 (2026-10-15), real, not accidental: confirmed
    # by grepping directly that this scanner's own first trigger word,
    # followed by "(4.5)", no longer appears anywhere under app/ -- one
    # real removal, app/telemetry/service.py's own Step-1.1b-era stub
    # marker (the real init_tracing()/record_trace() replaced that
    # whole stub). No other marker moved.
    # 67 -> 64 at Step 6.1 (2026-10-15), real, not accidental: diffed
    # `python hooks.py` output before/after directly before re-pinning.
    # Three real removals: app/admin/routes.py's own Step-1.1b-era stub
    # comment (one of this scanner's own third trigger word, one of its
    # fourth) was replaced wholesale when this task fixed its stale
    # deferred-to-6.5 reference (the temporary admin-key mechanism it
    # named is removed outright in this same task, not merely deferred);
    # backend/tests/test_config_guard.py's own ALLOWED_ADMIN_API_KEY_
    # CALLERS comment (the scanner's own fourth trigger word) is gone
    # along with that allow-list (admin_api_key_str() no longer exists
    # at all). Zero real additions: app/dashboard/ (this task's own new
    # package) and app/admin/routes.py's own real new code carry none of
    # this scanner's own four trigger words -- every other line-number
    # shift in the diff is the same pre-existing instance moving because
    # code was added above it in the same file, not a new one.
    # (Deliberately avoids spelling any of the four trigger words
    # literally anywhere in THIS comment, matching the established
    # convention immediately above in this same file -- this file is
    # itself scanned.)
    # 64 -> 65 at the out-of-band Phase 7 [SECURITY] L1 batch (2026-10-17),
    # real, not accidental: test_config_guard.py's own new ALLOWED_SMTP_
    # PASSWORD_CALLERS entry carries the identical "adding a file here is
    # a deliberate decision" allow-list comment every sibling entry in
    # that same file already uses -- a genuinely new instance of an
    # already-established marker (the scanner's own fourth trigger word),
    # not a duplicate. Confirmed by grepping that one file directly before
    # re-pinning this number: exactly one new line added, nothing else in
    # this task's own changed files (app/config.py, app/secrets_policy.py,
    # the new test files) carries any of the four trigger words.
    stdout = _run_hooks(REPO_ROOT)
    first_line = stdout.splitlines()[0]
    count = int(first_line.split()[0])
    assert count == 65, (
        f"expected 65 markers against the real repository, got {count} -- "
        f"either a real marker was added/removed, or the exclusion below "
        f"needs revisiting:\n{stdout}"
    )


def test_marker_scan_excludes_only_the_two_injection_detector_files_and_nothing_else(tmp_path):
    # Proves the exclusion is EXACTLY the two files it claims to be --
    # not a directory, not a filename pattern, not the trigger word
    # itself. A third file, at a path NOT in the exclusion set, built
    # with the identical trigger text, must still be caught. The marker
    # word is assembled at runtime (never spelled contiguously in THIS
    # file's own source), for the same reason the module docstring above
    # does: this file is scanned too, and is not in the exclusion set.
    marker_word = "NO" + "TE"
    trigger_line = f'MARKER = "IMPORTANT {marker_word} TO THE ASSISTANT READING THIS PAGE"\n'

    (tmp_path / "evals").mkdir()
    excluded_a = tmp_path / "backend" / "scripts" / "injection_detectors.py"
    excluded_b = tmp_path / "backend" / "tests" / "test_injection_detectors.py"
    not_excluded = tmp_path / "backend" / "app" / "some_other_module.py"
    for path in (excluded_a, excluded_b, not_excluded):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(trigger_line)

    stdout = _run_hooks(tmp_path)

    assert "injection_detectors.py" not in stdout
    assert "test_injection_detectors.py" not in stdout
    assert "some_other_module.py" in stdout
    first_line = stdout.splitlines()[0]
    assert int(first_line.split()[0]) == 1, (
        f"expected exactly 1 marker (the non-excluded file only): {stdout}"
    )
