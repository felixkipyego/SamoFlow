#!/bin/bash
# [SECURITY follow-up] CI fix, 2026-10-17: run-ci-locally.mjs's own
# documented limitation (docs/progress/widget.md, folded into
# PROJECT_SPEC.md's Step 5.1 entry) was "run on my own machine, not
# GitHub's ubuntu-24.04 runner -- it does not by itself prove the real
# run will be green". That gap was real, not theoretical: this session's
# widget CI failure (a Playwright keyboard-focus race in
# test/e2e/accessibility.spec.mjs) passed cleanly under plain
# run-ci-locally.mjs on a native macOS dev machine every single time,
# and only reproduced inside a genuine ubuntu-24.04 container under
# real multi-worker scheduling load -- confirming the OS/load mismatch
# was the actual reason local and CI disagreed, not a logic error in
# run-ci-locally.mjs itself.
#
# This script closes that gap: same widget.yml steps, same order, same
# pinned Node version (read from .nvmrc, not duplicated here), but
# executed inside a disposable ubuntu-24.04 container instead of the
# host OS -- the one thing a native run can never provide. Reuses
# run-ci-locally.mjs itself (never a second, hand-maintained step list)
# so the step-ordering guarantee that script already provides is
# inherited, not re-implemented.
#
# No new repo dependency: plain Docker + the stock `ubuntu:24.04` image,
# same convention as ops/security/scan-secrets.sh's pinned-image
# approach. The repo is copied into the container's own filesystem
# (never bind-mounted read-write) so a Linux `npm ci` here can never
# leave Linux-native binaries (esbuild, Playwright's browser) behind in
# the host's own node_modules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIDGET_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$WIDGET_DIR")"
NODE_VERSION="$(cat "$WIDGET_DIR/.nvmrc" | tr -d '[:space:]')"

cat > /tmp/run-ci-locally-docker-inner.sh << 'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates xz-utils rsync > /dev/null

ARCH=$(uname -m)
case "$ARCH" in
  x86_64) NODE_ARCH=x64 ;;
  aarch64) NODE_ARCH=arm64 ;;
  *) echo "unsupported arch $ARCH" >&2; exit 1 ;;
esac
curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz
mkdir -p /usr/local/lib/nodejs
tar -xJf /tmp/node.tar.xz -C /usr/local/lib/nodejs
export PATH="/usr/local/lib/nodejs/node-v${NODE_VERSION}-linux-${NODE_ARCH}/bin:$PATH"
echo "node: $(node -v)  npm: $(npm -v)"

mkdir -p /repo
rsync -a --exclude node_modules /repo-src/ /repo/
cd /repo/widget
export CI=true
npm ci
# Playwright's own browser-install step is itself one of widget.yml's
# `run:` lines, so run-ci-locally.mjs already executes it in order --
# nothing extra needed here beyond having npm/npx on PATH.
node scripts/run-ci-locally.mjs
INNER

docker run --rm \
  -e NODE_VERSION="$NODE_VERSION" \
  -v "$REPO_ROOT:/repo-src:ro" \
  -v "/tmp/run-ci-locally-docker-inner.sh:/inner.sh:ro" \
  ubuntu:24.04 \
  bash /inner.sh
