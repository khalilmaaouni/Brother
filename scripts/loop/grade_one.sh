#!/bin/bash
# usage (repo root): grade_one.sh <build.json> ; writes <wave>/grades/<name>.txt atomically and exits with the GRADER's code
# (Codex audit F9, 2026-09-27: it returned the final mv's status, so a refused build exited 0). A grade file that cannot be
# written is exit 2, NO-DATA: the caller finds no grade and must not read one.
b=$1; W=$(dirname "$(dirname "$b")"); n=$(basename "$b" -build.json); g="$W/grades/$n.txt"
# -u: a grade killed at its timeout keeps the lines it printed (2026-10-03: two CV1.b grades left 0 byte files)
python3 -u ~/.claude/bin/grade_build.py "$b" > "$g.part" 2>&1; rc=$?
{ echo "exit=$rc" >> "$g.part" && mv "$g.part" "$g"; } || { echo "grade_one: NO-DATA, could not write $g" >&2; exit 2; }
exit $rc
