#!/bin/bash
# usage (repo root): grade_all_par.sh <wave dir> [parallel=3] ; grades every ungraded out/*-build.json N at a time, one verdict line each.
# Exit 0 only when every build has a grade that recorded exit=0; 1 when any build ended with no grade file or the grader refused
# it (a batch that graded nothing, or refused something, must never read as done); 2 NO-DATA when the wave holds no build.
W=$1; P=${2:-3}; mkdir -p "$W/grades"; rc=0; seen=0
for b in "$W"/out/*-build.json; do [ -e "$b" ] || continue; n=$(basename "$b" -build.json); [ -s "$W/grades/$n.txt" ] || echo "$b"; done | xargs -P "$P" -n 1 ~/.claude/bin/grade_one.sh
for b in "$W"/out/*-build.json; do [ -e "$b" ] || continue; seen=1; n=$(basename "$b" -build.json); g="$W/grades/$n.txt"
  if [ ! -s "$g" ]; then printf "%-12s NO-DATA: no grade file\n" "$n"; rc=1; continue; fi
  [ "$(tail -n 1 "$g")" = "exit=0" ] || rc=1
  printf "%-12s %s | %s\n" "$n" "$(grep -E '^(PASS|FAIL)' "$g" | tail -n 1 | cut -c1-110)" "$(grep -E '^MUTATIONS' "$g")"; done
[ "$seen" = 1 ] || { echo "NO-DATA: no build in $W/out"; exit 2; }
exit $rc
