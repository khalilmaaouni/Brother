#!/bin/bash
# A/B/C setup (owner order 2026-09-23). Three worktrees from ONE base commit (the launch head after Phase 2 and the
# deploy), one hub branch each so land_batch has a real upstream, the sample frozen from that base. Never starts a driver.
set -u
E=~/.claude/evidence/loop-run-2026-09-23; AB=$E/ab; W=~/Brother/.claude/worktrees/brother-unify-1.1
bash ~/.claude/bin/loop_guard.sh check || { echo "lease held; refusing"; exit 1; }
cd "$W" || exit 1
git fetch -q hub; [ "$(git rev-list --left-right --count hub/refactor/brother-unified-1.1...HEAD)" = "0	0" ] || { echo "launch != hub; refusing"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "launch tree dirty; refusing"; exit 1; }
BASE=$(git rev-parse HEAD); echo "$BASE" > $AB/BASE.txt; echo "BASE $BASE"
python3 -B $AB/pick_sample.py "$W" 12 > $AB/candidates.json || { echo "sample pick failed"; exit 1; }
python3 -B $AB/validate_sample.py $AB/candidates.json "$BASE" 8 > $AB/sample.json || { echo "fewer than 8 valid sub units"; exit 1; }
python3 -c "import json;print('DROPPED', json.load(open('$AB/sample.json'))['dropped'])"
python3 -c "import json;d=json.load(open('$AB/sample.json'));print('SAMPLE', ' '.join(r['sub'] for r in d['sample']))"
for a in a b c; do
  T=~/Brother/.claude/worktrees/abc-$a; BR=exp/abc-2026-09-23-$a
  [ -e "$T" ] && { echo "$T exists; refusing to reuse a tree"; exit 1; }
  git -C ~/Brother worktree add -q "$T" -b "$BR" "$BASE" || { echo "worktree add $a failed"; exit 1; }
  git -C "$T" push -q -u hub "$BR" > $AB/push-setup-$a.log 2>&1; echo "arm $a: tree $T branch $BR push exit=$?"
  git -C "$T" rev-parse --abbrev-ref --symbolic-full-name '@{u}'
done
