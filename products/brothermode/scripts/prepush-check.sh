#!/bin/sh
# The one command to run before every push, and the reason it exists.
#
# M18 recorded that the local gate cannot see a stale manifest: its integrity
# check reads the INSTALLED copy, whose manifest matches by construction, while
# continuous integration reads the branch itself. M18's remedy was a written
# rule saying "run verify-install before you push". That rule was written, and
# then its own author broke it twice within a day, which is the whole argument
# of this project in miniature: a rule in a document is not a control, and a
# control is a check that runs.
#
# So this is the check. It refuses rather than reminds.
#
# Exit 0 the tree is publishable. Exit 1 something is stale and it says what.
# Exit 2 the tree is dirty, which is its own answer: commit first, because a
# manifest built over uncommitted work describes a tree nobody else will get.
set -u

say() { printf '%s\n' "prepush-check: $*"; }

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || { say "cannot enter the repository root"; exit 1; }

dirt=$(git status --porcelain --untracked-files=no)
if [ -n "$dirt" ]; then
    say "REFUSING: tracked files are uncommitted. Commit them first, because"
    say "the manifest must describe the tree that will actually be pushed:"
    printf '%s\n' "$dirt" | sed 's/^/    /'
    exit 2
fi

if ! out=$(sh scripts/verify-install.sh 2>&1); then
    printf '%s\n' "$out" | grep -E "FAIL|mismatch|EXTRA|MISSING" | sed 's/^/    /'
    say "REFUSING: the committed manifest does not describe the committed tree."
    say "Fix it in this order, which is the order M18 exists to enforce:"
    say "  sh scripts/checksums.sh CHECKSUMS.sha256"
    say "  git add CHECKSUMS.sha256 && git commit"
    say "  sh scripts/prepush-check.sh"
    exit 1
fi

say "PASSED: the committed manifest describes the committed tree. Safe to push."
exit 0
