#!/bin/bash
# usage (repo root): grade_all.sh <wave dir> ; grades every out/*-build.json not yet graded, one verdict line each.
# Exit 0 only when EVERY build's grade recorded exit=0; 1 when any was refused; 2 NO-DATA when the wave holds no build
# (Codex audit F9, 2026-09-27: it returned its last printf's status, so a refused build exited 0).
W=$1; mkdir -p "$W/grades"; rc=0; seen=0
for b in "$W"/out/*-build.json; do
  [ -e "$b" ] || continue; seen=1
  n=$(basename "$b" -build.json); g="$W/grades/$n.txt"
  [ -s "$g" ] || ~/.claude/bin/grade_one.sh "$b"
  [ "$(tail -n 1 "$g" 2>/dev/null)" = "exit=0" ] || rc=1
  printf "%-12s %s | %s\n" "$n" "$(grep -E '^(PASS|FAIL)' "$g" 2>/dev/null | tail -n 1 | cut -c1-110)" "$(grep -E '^MUTATIONS' "$g" 2>/dev/null)"
done
[ "$seen" = 1 ] || { echo "NO-DATA: no build in $W/out"; exit 2; }
exit $rc
