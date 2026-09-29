#!/bin/sh
# SPDX-License-Identifier: MIT
#
# One command to the point where the harness can be previewed: clone the `stable` branch, write a
# default configuration, and dry-run the install. It never installs anything itself — the last
# thing it prints is the command that does. Shape borrowed from pmstack's `install.sh`.
#
#   curl -fsSL https://raw.githubusercontent.com/JakeSelby/model-citizen/stable/scripts/install.sh | sh
#
# Environment:
#   HARNESS_CHECKOUT              where the checkout lives      (default ~/repos/agent-harness)
#   HARNESS_BRANCH                branch to track               (default stable)
#   HARNESS_REPO_URL              clone source
#   HARNESS_INSTALL_NO_HOMEBREW   pass --no-brew to the dry run
#   HARNESS_INSTALL_NO_APPS       pass --no-apps to the dry run
#   HARNESS_HOME                  the harness's own config/state home, for fixtures
#
# Anything after `sh -s --` is passed through to `bin/citizen install --dry-run`.
set -eu

REPO_URL="${HARNESS_REPO_URL:-https://github.com/JakeSelby/model-citizen.git}"
BRANCH="${HARNESS_BRANCH:-stable}"
CHECKOUT="${HARNESS_CHECKOUT:-$HOME/repos/agent-harness}"
CONFIG="${HARNESS_HOME:-$HOME}/.config/agent-harness/config.json"

fail() {
    echo "install.sh: $1" >&2
    exit 1
}

# 1. Requirements. Both are hard: the harness runs from a git checkout, with python3.
command -v git >/dev/null 2>&1 ||
    fail "requirements: git is not installed; install it and run this again"
command -v python3 >/dev/null 2>&1 ||
    fail "requirements: python3 is not installed; install Python 3.9 or newer and run this again"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null ||
    fail "requirements: python3 is older than 3.9; install Python 3.9 or newer and run this again"
case "$(uname -s)" in
    Darwin | Linux) ;;
    *) fail "requirements: macOS and Linux only; native Windows is unsupported" ;;
esac

# 2. Checkout. An existing one is updated in place rather than re-cloned, so a second run of this
#    script is an update and nothing that is already there is thrown away.
if [ -d "$CHECKOUT/.git" ]; then
    echo "==> updating $CHECKOUT ($BRANCH)"
    git -C "$CHECKOUT" fetch --quiet "$REPO_URL" "$BRANCH" ||
        fail "update: could not fetch $BRANCH from $REPO_URL"
    git -C "$CHECKOUT" merge --ff-only --quiet FETCH_HEAD ||
        fail "update: $CHECKOUT cannot fast-forward to $BRANCH; reconcile it by hand, then re-run"
elif [ -e "$CHECKOUT" ]; then
    fail "clone: $CHECKOUT exists and is not a git checkout; move it or set HARNESS_CHECKOUT"
else
    echo "==> cloning $BRANCH into $CHECKOUT"
    mkdir -p "$(dirname "$CHECKOUT")" ||
        fail "clone: could not create $(dirname "$CHECKOUT")"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$CHECKOUT" ||
        fail "clone: could not clone $BRANCH from $REPO_URL"
fi

# 3. Configuration. An existing config is never rewritten; `init --force` is the user's call.
if [ -f "$CONFIG" ]; then
    echo "==> keeping the configuration already at $CONFIG"
else
    echo "==> writing a default configuration"
    "$CHECKOUT/bin/citizen" init --yes ||
        fail "init: could not write $CONFIG"
fi

# 4. Preview. Only ever the dry run: this script does not change a runtime's settings.
FLAGS=""
if [ -n "${HARNESS_INSTALL_NO_HOMEBREW:-}" ]; then
    FLAGS="$FLAGS --no-brew"
fi
if [ -n "${HARNESS_INSTALL_NO_APPS:-}" ]; then
    FLAGS="$FLAGS --no-apps"
fi
echo "==> previewing the install (nothing is written)"
# shellcheck disable=SC2086
"$CHECKOUT/bin/citizen" install --dry-run $FLAGS "$@" ||
    fail "preview: \`$CHECKOUT/bin/citizen install --dry-run\` failed; nothing has been changed"

cat <<EOF

Model Citizen is checked out at $CHECKOUT and configured. Nothing is installed yet.

  review   $CHECKOUT/bin/citizen sync --dry-run
  install  $CHECKOUT/bin/citizen install
  undo     $CHECKOUT/bin/citizen uninstall

Set who you are and what you prefer with \`$CHECKOUT/bin/citizen config set\`; the fields still at their
example value are listed above.
EOF
