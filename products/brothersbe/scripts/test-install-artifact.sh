#!/bin/sh
# test-install-artifact.sh: does a git-archive artifact of this repository
# install clean into an empty directory, with nothing more than "extract and
# run"?
#
# Why this exists: docs/RELEASE.md documents the pin command
# (`git clone --branch <tag>`), but nothing before this script ever ran that
# path end to end and checked what came out the other side. The kill
# criterion the plugin conversion plan states for this wave is "an install
# that needs a manual global settings edit"; this script is the check for
# that claim, not a restatement of it. Step 2 below IS the whole install: it
# writes nothing outside the one fresh directory it creates, edits nothing
# in $HOME, and reads no environment variable to find its target.
#
# What this proves, precisely: the tree git tracks at the ref under test
# (default HEAD) extracts on its own into a location holding nothing else,
# its own CHECKSUMS.sha256 verifies against its own bytes there
# (scripts/verify-install.sh, run from the FRESH copy so it is checking the
# artifact and not this checkout), and `bin/sbe doctor` reports no FAIL from
# that fresh copy. Doctor's own exit code already encodes "no FAIL" (0 iff
# no control in its table reads FAIL; see src/brothersbe/cli.py), so
# checking the exit code is sufficient and this script does not scrape its
# output looking for the word.
#
# What this does NOT prove: that the manifest itself is authentic (the same
# stated limit verify-install.sh carries), or that an upgrade over an
# existing install behaves the same way (that is
# scripts/test-upgrade-rollback.sh, a separate fixture on purpose, because
# "installs clean" and "upgrades clean" are different claims that can pass
# or fail independently).
#
# Runs identically from a normal `git clone` and from a git worktree (whose
# .git is a plain FILE naming ../.git/worktrees/<name>, not a directory):
# `git archive` resolves the ref through git's own plumbing exactly like
# `git log` or `git status` do, and never reads the file at .git to learn
# anything beyond where the object database is, so a worktree checkout is
# exactly as good an archive source as a plain clone.
#
# POSIX sh only, no bashisms: no arrays, no [[ ]], no local, no process
# substitution, no here-strings. Same portability intent as
# scripts/verify-install.sh, which this script calls rather than
# reimplementing.
#
# Usage:
#   scripts/test-install-artifact.sh [ref]
#
#   ref  the git ref to archive; defaults to HEAD (the artifact a
#        `git archive` of the commit under test would produce, which is
#        what a tagged release ships).

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

REF="${1:-HEAD}"

if ! git rev-parse --verify --quiet "$REF^{commit}" >/dev/null 2>&1; then
    echo "test-install-artifact: '$REF' does not resolve to a commit in this repository; nothing to archive" >&2
    exit 2
fi
RESOLVED=$(git rev-parse --short "$REF")

WORKDIR=$(mktemp -d 2>/dev/null || echo "/tmp/sbe-test-install-artifact-work.$$")
mkdir -p "$WORKDIR"
TARGET="$WORKDIR/install"
mkdir -p "$TARGET"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

echo "test-install-artifact: archiving $REF ($RESOLVED) into a fresh, otherwise-empty directory"
echo "test-install-artifact: target: $TARGET"
git archive --format=tar "$REF" | (cd "$TARGET" && tar xf -)

FAILED=0

if [ ! -f "$TARGET/CHECKSUMS.sha256" ]; then
    echo "test-install-artifact: FAIL, no CHECKSUMS.sha256 shipped at $REF ($RESOLVED); nothing to verify the install against" >&2
    FAILED=1
elif [ ! -f "$TARGET/scripts/verify-install.sh" ]; then
    echo "test-install-artifact: FAIL, scripts/verify-install.sh did not ship at $REF ($RESOLVED)" >&2
    FAILED=1
else
    echo "test-install-artifact: running verify-install.sh from the fresh copy against its own shipped manifest"
    if sh "$TARGET/scripts/verify-install.sh"; then
        echo "test-install-artifact: verify-install.sh PASSED on the fresh install"
    else
        echo "test-install-artifact: FAIL, verify-install.sh did not pass on the fresh install (see its output above)" >&2
        FAILED=1
    fi
fi

if [ ! -f "$TARGET/bin/sbe" ]; then
    echo "test-install-artifact: FAIL, bin/sbe did not ship at $REF ($RESOLVED)" >&2
    FAILED=1
else
    echo "test-install-artifact: running bin/sbe doctor from the fresh copy (invoked as \`python3 bin/sbe\`, not via its own shebang, so this check does not depend on tar having preserved the execute bit; run with cwd set to the fresh copy, because doctor's own git check reads the CALLING process's cwd, not its own script location, so running it from this script's cwd would silently grade this checkout's git tree instead of the artifact's)"
    if (cd "$TARGET" && python3 "$TARGET/bin/sbe" doctor); then
        echo "test-install-artifact: bin/sbe doctor reported no FAIL"
    else
        echo "test-install-artifact: FAIL, bin/sbe doctor reported a FAIL from the fresh install (see its output above)" >&2
        FAILED=1
    fi
fi

echo ""
if [ "$FAILED" -ne 0 ]; then
    echo "test-install-artifact: FAILED for $REF ($RESOLVED). An artifact of this ref does not install clean; see the FAIL line(s) above." >&2
    exit 1
fi
echo "test-install-artifact: PASSED for $REF ($RESOLVED). \`git archive\` extracted into an empty directory with no other install step, scripts/verify-install.sh reported PASSED against the manifest that shipped in that same archive, and bin/sbe doctor reported no FAIL, all without writing to \$HOME, ~/.claude, or any location outside the one temporary directory this script created and removed on exit."
