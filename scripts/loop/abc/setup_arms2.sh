#!/bin/bash
# Second measurement (owner 18:2x): arms A2 (Claude only on the fixed code) and O (cost optimized), worktrees from the
# launch head that carries the fixes; the sample is re-validated red on this head. Never starts a driver.
set -u
AB=~/.claude/evidence/loop-run-2026-09-23/ab; W=~/Brother/.claude/worktrees/brother-unify-1.1
cd "$W" || exit 1; git fetch -q hub
[ "$(git rev-list --left-right --count hub/refactor/brother-unified-1.1...HEAD)" = "0	0" ] || { echo "launch != hub"; exit 1; }
B2=$(git rev-parse HEAD); echo "$B2" > $AB/BASE2.txt; echo "BASE2 $B2"
python3 -B $AB/validate_sample.py $AB/candidates.json "$B2" 8 > $AB/sample2.json || { echo "sample not valid on BASE2"; exit 1; }
python3 -c "import json;a=[r['sub'] for r in json.load(open('$AB/sample.json'))['sample']];b=[r['sub'] for r in json.load(open('$AB/sample2.json'))['sample']];print('SAME SAMPLE' if a==b else 'SAMPLE DIFFERS %s %s'%(a,b))"
for a in a2 o; do
  T=~/Brother/.claude/worktrees/abc-$a; BR=exp/abc-2026-09-23-$a
  [ -e "$T" ] && { echo "$T exists"; exit 1; }
  git -C ~/Brother worktree add -q "$T" -b "$BR" "$B2" || exit 1
  git -C "$T" push -q -u hub "$BR" > $AB/push-setup-$a.log 2>&1; echo "arm $a push exit=$? upstream $(git -C "$T" rev-parse --abbrev-ref '@{u}')"
done
