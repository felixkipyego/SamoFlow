#!/bin/bash
# ops/deploy/preflight.sh -- [SECURITY] L1 (out-of-band Phase 7 batch,
# 2026-10-17), PART B: run before every deploy (docs/deploy/RUNBOOK.md's
# own "First deploy" section, and before every later `docker compose up`
# on the real server), because Felix chose to keep secrets in `.env`
# rather than move to Docker secrets (D-7decided-env-secrets-kept) --
# this script is the real hardening that decision is conditioned on.
# Fails loud on the first problem found; never prints a secret value,
# only variable NAMES.
#
#   ./ops/deploy/preflight.sh
#
# Checks, in order:
#   1. .env exists.
#   2. .env's own file permissions are not looser than 600 (owner
#      read/write only -- no group or other access at all).
#   3. .env is owned by the user running this script (the real deploy
#      user, per RUNBOOK's own "Non-root deploy user" step -- checked
#      against whoever is ACTUALLY running this script, not a hard-coded
#      username, so this stays correct even if that username ever
#      changes).
#   4. .env is not tracked by git, and -- since RUNBOOK's own deploy
#      flow is a real `git clone` onto the server, so .env genuinely
#      sits inside a live git working tree -- is not merely untracked
#      but exposed to a careless `git add .`/`git add -A` sweeping it
#      in (confirmed covered by .gitignore, not assumed from memory).
#   5. Every required secret-class variable is present, and none holds
#      a known placeholder/weak value or is shorter than its own stated
#      minimum -- reusing backend/app/secrets_policy.py directly, via
#      check_env_secrets.py (this directory), so this is the SAME policy
#      Settings' own production startup gate enforces, never a second,
#      independently-maintained copy of the same rules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

# (1) exists
[ -f "$ENV_FILE" ] || fail "$ENV_FILE not found -- copy .env.example to .env and fill it in first"
echo "PASS: $ENV_FILE exists"

# (2) permissions no looser than 600
mode=$(stat -c '%a' "$ENV_FILE")
if [ "$mode" != "600" ]; then
    fail "$ENV_FILE permissions are $mode, expected exactly 600 -- run: chmod 600 $ENV_FILE"
fi
echo "PASS: $ENV_FILE permissions are 600"

# (3) owned by the user running this script
owner=$(stat -c '%U' "$ENV_FILE")
current_user=$(id -un)
if [ "$owner" != "$current_user" ]; then
    fail "$ENV_FILE is owned by '$owner', not the current user '$current_user' -- run: chown $current_user $ENV_FILE"
fi
echo "PASS: $ENV_FILE is owned by the current user ($current_user)"

# (4) never tracked by git; never unignored in a live working tree
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git -C "$REPO_ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
        fail ".env is TRACKED by git in $REPO_ROOT -- this means it has already been committed. Remove it from version control immediately (git rm --cached .env) and rotate every secret it ever held, since it may be reachable from history on a remote."
    fi
    if ! git -C "$REPO_ROOT" check-ignore -q .env; then
        fail ".env is untracked but NOT covered by .gitignore in $REPO_ROOT -- a careless 'git add .'/'git add -A' would commit it. Add '.env' to .gitignore before proceeding."
    fi
    echo "PASS: .env is untracked and covered by .gitignore (not committable by accident)"
else
    echo "PASS: $REPO_ROOT is not a git working tree -- no git-related risk to .env here"
fi

# (5) every required secret-class variable present, none weak/placeholder
# -- delegates to check_env_secrets.py (same directory), which imports
# backend/app/secrets_policy.py directly: one source of truth, reused,
# not copied into this shell script's own second implementation.
if ! python3 "$SCRIPT_DIR/check_env_secrets.py" "$ENV_FILE"; then
    exit 1
fi
echo "PASS: every required secret-class variable is present and not a known placeholder/weak value"

echo "preflight.sh: all checks passed."
