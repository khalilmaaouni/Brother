#!/bin/sh
# The body of the pre-push hook, kept HERE rather than in .git/hooks so it is
# version controlled, reviewable, and testable. The installed hook is two lines
# that call this.
#
# FOUNDER DECISION 2026-08-29, his words: "I will select Gates at the boundaries
# for the watchdogs." This file is what that decision means in practice. The
# estate already had the checks; what it lacked was a moment when they fire, and
# a push is the sharpest moment available: the last point a mistake is still
# cheap and the first point it becomes somebody else's problem.
#
# EXIT CODES, and the middle one is the whole discipline:
#   0  clear, the push proceeds
#   1  BLOCKED, something would leave this machine that must not
#   2  NO-DATA, a check could not run, which is NOT a pass and so refuses too
#
# A BLOCK IS A LANE CHANGE, NEVER A STOP (his standing rule). So a refusal
# prints what to do next rather than only what went wrong.
set -u
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0   # not a repo, not our business
GATE="$ROOT/scripts/pre_push_gate.py"

if [ ! -f "$GATE" ]; then
  # NOT a silent pass. The hook was installed in a repository that does not
  # carry the gate, which is a real misconfiguration, and the person deserves
  # to know rather than to believe they are protected.
  echo "pre-push: NO-DATA, $GATE is not present, so nothing was checked." >&2
  echo "  This repository has the hook but not the gate. Either install the" >&2
  echo "  gate or remove the hook: sh scripts/install_gate_hook.sh --uninstall" >&2
  exit 2
fi

# git's pre-push hook protocol feeds "<remote name> <remote URL>" as $1 $2.
# Passed through so the gate's edition check (docs/plan/HUB-MIGRATION-PLAN-
# 2026-08-30.md step 5) knows which remote this push actually targets; a
# manual run with neither argument still works, the flags just come through
# empty.
python3 "$GATE" --cwd "$ROOT" --remote-name "${1:-}" --remote-url "${2:-}"
code=$?
[ "$code" -eq 0 ] && exit 0

echo "" >&2
echo "The push was refused. This is a lane change, not a stop:" >&2
if [ "$code" -eq 2 ]; then
  echo "  A check could NOT RUN. That is not the same as passing, so the push" >&2
  echo "  stopped. Fix what could not be read, then push again." >&2
else
  echo "  * a collision means somebody else's work is on the remote: pull first" >&2
  echo "  * a correctness finding is in your own change: fix it and push again" >&2
  echo "  * a remote-rules block is a configuration deadlock, not your change," >&2
  echo "    and it needs the owner's decision rather than another attempt" >&2
fi
echo "  To push anyway, deliberately and on the record: git push --no-verify" >&2
exit "$code"
