#!/bin/sh
# verify_tree: never run a verification worktree on top of a stale registration.
#
# Row M2 of the 2026-09-07 reflection. `rm -rf` of a verify worktree left
# git's own registration behind; the next `git worktree add` at the same
# path failed, and a command chain that keeps going after a failed
# worktree step landed on main instead, so main's unisolated hook suite
# wrote real rows into the real log. This script is the one place every
# orchestrator verification adds a worktree from, so that failure mode
# cannot recur one call site at a time.
#
# usage: verify_tree.sh PATH REF
#   PATH  where the detached worktree goes (created fresh; any stale
#         registration or leftover directory at PATH is cleared first)
#   REF   a branch, a remote ref, or a commit
#
# STDOUT carries exactly one line on success: the absolute PATH, so a
# caller can write `cd "$(scripts/verify_tree.sh /private/tmp/x
# origin/main)"` directly, or read that same single line as "the path,
# printed last". Everything else (progress, the resolved commit) goes to
# stderr, which is why stdout stays safe to feed straight into cd.
#
# Exit codes: 0 the worktree exists at PATH, checked out at REF's resolved
# commit. 2 a usage problem (wrong arg count, not inside a git repo). 1 the
# add itself failed; nothing else about the repository is touched beyond
# the prune and stale-registration cleanup this script always performs
# first regardless of what REF turns out to be.
set -u

if [ "$#" -ne 2 ]; then
  echo "usage: verify_tree.sh PATH REF" >&2
  exit 2
fi

TARGET="$1"
REF="$2"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "verify_tree: not inside a git repository" >&2
  exit 2
}

case "$TARGET" in
  /*) ABS_TARGET="$TARGET" ;;
  *) ABS_TARGET="$(pwd)/$TARGET" ;;
esac

cd "$ROOT" || exit 1

# Always prune first: this is the exact step the mistake skipped, and
# running it before every add costs nothing when there is nothing to prune.
git worktree prune >&2 2>&1

# A registration can survive prune if the directory still exists but is not
# what git left there (or the reverse: the directory exists with no
# registration, left by a plain `rm -rf` that raced the worktree machinery).
# Clear both shapes explicitly rather than trusting prune alone.
if git worktree list --porcelain 2>/dev/null | grep -qx "worktree $ABS_TARGET"; then
  echo "verify_tree: clearing a live registration at $ABS_TARGET" >&2
  git worktree remove --force "$ABS_TARGET" >&2 2>&1
  git worktree prune >&2 2>&1
fi
if [ -e "$ABS_TARGET" ]; then
  echo "verify_tree: clearing an unregistered leftover at $ABS_TARGET" >&2
  rm -rf "$ABS_TARGET"
fi

if ! git worktree add --detach "$ABS_TARGET" "$REF" >&2 2>&1; then
  echo "verify_tree: git worktree add failed for ref '$REF' at $ABS_TARGET" >&2
  exit 1
fi

RESOLVED="$(git -C "$ABS_TARGET" rev-parse HEAD 2>/dev/null)"
echo "verify_tree: $REF resolved to $RESOLVED at $ABS_TARGET" >&2

printf '%s\n' "$ABS_TARGET"
exit 0
