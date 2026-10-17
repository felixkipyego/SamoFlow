#!/bin/bash
# ops/security/scan-secrets.sh -- [SECURITY] secret scan via gitleaks' own
# official container image, pinned to an exact version AND digest (never
# :latest) -- adds no new repo dependency: nothing is installed, Docker
# pulls and runs the image fresh each time. Built for PART B of the
# 2026-10-17 [SECURITY] batch; intended for ad hoc use and for Step 7.2's
# own full security run to re-run as-is.
#
#   ./ops/security/scan-secrets.sh                    # full history (default)
#   ./ops/security/scan-secrets.sh "<base-sha>..<head-sha>"   # one git log range only
#
# The optional first argument, added at the out-of-band maintenance batch
# (2026-10-17, PART D), is passed straight to gitleaks' own `--log-opts`:
# any `git log` revision range. Added so the new PR-triggered
# .github/workflows/secret-scan.yml job can scan just a PR's own new
# commits -- re-scanning this repo's full, already-reviewed history (300+
# commits and growing) on every single PR would be slow and pointless, and
# reusing this exact script (same pinned image, same .gitleaksignore) keeps
# one source of truth instead of a second, duplicated gitleaks invocation.
# With no argument, behavior is byte-identical to before this change: a
# full-history scan from HEAD.
#
# Already-reviewed false positives are suppressed via the committed
# .gitleaksignore (repo root) -- each entry there carries its own
# one-line reason, confirmed by reading the real historical source at
# that exact commit/file/line, never assumed from the rule name or
# entropy score alone. Exits non-zero (gitleaks' own default exit code,
# propagated by `set -e` below) if anything NOT already in that file is
# found -- a genuinely new finding should stop a run, never be silently
# absorbed. NEVER passes `--redact=0`: this script's own stdout, and the
# JSON report it writes, must never carry a real secret value in the
# clear, even if a real one is ever found.
set -euo pipefail
LOG_OPTS="${1:-}"
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"

# [SECURITY] Pinned to an exact version AND its digest -- confirmed live
# via `docker pull`/`docker images --digests` before this script was
# written, not copied from memory. Never :latest: this script's own
# behavior (and the rule set gitleaks ships with) must not silently
# change between runs just because a new image was published upstream.
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.21.2@sha256:0e99e8821643ea5b235718642b93bb32486af9c8162c8b8731f7cbdc951a7f46"

# A git WORKTREE's own .git is a plain text file naming an ABSOLUTE host
# path under the real repo's own .git/worktrees/<name> -- if that real
# repo lives OUTSIDE this worktree's own directory tree (this project's
# own layout: a worktree as a SIBLING directory of the real checkout),
# a container with only this worktree bind-mounted cannot resolve that
# absolute path at all. Mount the common ancestor instead, so the
# absolute path resolves identically inside the container. Confirmed
# live, not assumed, while building this script: without this fix,
# gitleaks silently reported "no leaks found" after actually failing to
# open the repository at all -- a false-negative outcome far worse than
# a loud error, which is exactly why this is handled explicitly rather
# than left as an edge case someone might hit later.
GIT_COMMON_DIR="$(git rev-parse --git-common-dir)"
case "$GIT_COMMON_DIR" in
    /*) ;;
    *) GIT_COMMON_DIR="$REPO_ROOT/$GIT_COMMON_DIR" ;;
esac
case "$GIT_COMMON_DIR" in
    "$REPO_ROOT"/*|"$REPO_ROOT")
        MOUNT_ROOT="$REPO_ROOT"
        ;;
    *)
        # The layout this project actually uses: a worktree as a sibling
        # of the real checkout -- mount one level up so both resolve at
        # their real absolute paths. A more exotic worktree layout (the
        # real repo several levels above, not a direct parent) would
        # need this widened; not built speculatively since it is not
        # this project's own layout today (rule 11).
        MOUNT_ROOT="$(dirname "$REPO_ROOT")"
        ;;
esac

REPORT_DIR="$(mktemp -d)"
trap 'rm -rf "$REPORT_DIR"' EXIT

GITLEAKS_ARGS=(
    detect
    --source="$REPO_ROOT"
    --redact
    --report-format json
    --report-path /out/gitleaks-report.json
    -i "$REPO_ROOT/.gitleaksignore"
)

if [ -n "$LOG_OPTS" ]; then
    GITLEAKS_ARGS+=(--log-opts="$LOG_OPTS")
    echo "Scanning git log range '$LOG_OPTS' only via $GITLEAKS_IMAGE ..."
else
    commit_count="$(git rev-list --count HEAD)"
    echo "Scanning full git history ($commit_count commits on HEAD's own ancestry) via $GITLEAKS_IMAGE ..."
fi

docker run --rm \
    -v "$MOUNT_ROOT:$MOUNT_ROOT" \
    -v "$REPORT_DIR:/out" \
    "$GITLEAKS_IMAGE" \
    "${GITLEAKS_ARGS[@]}"

if [ -n "$LOG_OPTS" ]; then
    echo "Clean: no un-allowlisted secrets found in range '$LOG_OPTS'."
else
    echo "Clean: no un-allowlisted secrets found across $commit_count commits."
fi
