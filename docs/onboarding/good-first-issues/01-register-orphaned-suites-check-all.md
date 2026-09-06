Register two orphaned test suites in scripts/check_all.sh

`scripts/test_brother_paths.py` and `scripts/test_e53_lesson_ab.py` both
exist on disk, both run clean standalone (25 tests and 20 tests, verified
on 2026-09-06), and neither is named anywhere in `scripts/check_all.sh` or
`scripts/required_fast.sh`. A suite nobody registers is a suite nobody
runs, however carefully it was written, and `check_all.sh` is the one
place this repository's full battery is supposed to name every check by
hand.

This gap is not tracked as its own row on the readiness roadmap; it is a
sibling of row S31 (docs/plan/READINESS-ROADMAP-2026-08-29.json), found and
independently re-verified against the tree on 2026-09-06 the same way row
S31's own earlier draft (docs/plan/GOOD-FIRST-ISSUES-2026-09-05.md, items 1
and 2) was: by diffing scripts/test_*.py against check_all.sh's own
run_check lines. Three of that draft's five suites are registered now, and
a fourth currently fails standalone, so it is left out here rather than
carried forward unverified.

Files: `scripts/check_all.sh` only, adding two `run_check` lines next to
the other suites already registered there (follow the existing pattern,
for example `run_check "brother-paths" python3 scripts/test_brother_paths.py -v`).

Done check: `grep -c "test_brother_paths\.py\|test_e53_lesson_ab\.py" scripts/check_all.sh`
prints 2, and `sh scripts/check_all.sh` still reaches its own summary line
naming both new checks as PASS.

label: good first issue
