Register scripts/test_acceptance_trial_assign.py in scripts/check_all.sh

`scripts/test_acceptance_trial_assign.py` covers the Acceptance
Compression trial assignment and validation harness plus the frozen
success rule staying byte for byte what it was when frozen. It exists on
disk, runs clean standalone (12 tests, verified on 2026-09-06), and writes
only under a temporary directory (its own docstring says so), so it is
safe for a fresh clone to run. It is not named anywhere in
`scripts/check_all.sh` or `scripts/required_fast.sh`.

This is the same orphaned-suite gap as issue 1 in this same folder, on a
different file, and is not tracked as its own row on the readiness
roadmap: a sibling of row S31 (docs/plan/READINESS-ROADMAP-2026-08-29.json),
found the same way issue 1 was found (diffing scripts/test_*.py against
check_all.sh's own run_check lines) and independently verified against the
tree on 2026-09-06.

Files: `scripts/check_all.sh` only, adding one `run_check` line next to
the other suites already registered there (follow the existing pattern,
for example `run_check "acceptance-trial-assign" python3 scripts/test_acceptance_trial_assign.py -v`).

Done check: `grep -c test_acceptance_trial_assign.py scripts/check_all.sh`
prints 1, and `sh scripts/check_all.sh` reaches its own summary line naming
the new check as PASS.

label: good first issue
