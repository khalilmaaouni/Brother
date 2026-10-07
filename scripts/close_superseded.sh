#!/bin/bash
# close_superseded.sh [--dry-run] [list]: the OWNER's bulk close of pull requests the triage
# (scripts/pr_park_triage.py) proved landed or superseded. Run by the owner, never by a session
# (spec: docs/plan/specs/ACC4.md, ACC4.b). Each list line: "<number> <reason>"; the reason is posted
# as the closing comment, as ONE quoted argument, never through a shell. A pull request that is not
# OPEN is skipped and reported. Stops at the first failure. Prints "DONE: n of m" and exits 0 only
# when every listed pull request was closed, skipped or (dry run) would be closed.
# --dry-run prints "WOULD CLOSE #n" lines and runs no write verb at all.
set -u
REPO=${PR_PARK_REPO:-khalilmaaouni/brother-hub}
DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
LIST="${1:-$(dirname "$0")/../docs/plan/ACC4-PARK-LIST.txt}"
[ -r "$LIST" ] || { echo "NO-DATA: no list at $LIST"; exit 2; }
n=0; done_=0
while read -r pr reason; do
  case "$pr" in ''|\#*) continue;; esac
  n=$((n+1))
  st=$(gh pr view "$pr" -R "$REPO" --json state -q .state 2>&1) || { echo "STOP at #$pr: cannot read it ($st)"; break; }
  [ "$st" = OPEN ] || { echo "SKIP #$pr: state is $st"; done_=$((done_+1)); continue; }
  if [ "$DRY" = 1 ]; then echo "WOULD CLOSE #$pr: $reason"; done_=$((done_+1)); continue; fi
  gh pr close "$pr" -R "$REPO" --comment "$reason" </dev/null && { echo "CLOSED #$pr"; done_=$((done_+1)); } || { echo "STOP at #$pr: close refused"; break; }
done < "$LIST"
echo "DONE: $done_ of $n listed PR(s) $([ "$DRY" = 1 ] && echo 'would close' || echo 'closed or already closed')"
[ "$done_" -eq "$n" ]
