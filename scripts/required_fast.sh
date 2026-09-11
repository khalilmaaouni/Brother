#!/bin/sh
# required-fast: the cheap mandatory pre-merge contract. Every merge into
# main runs this locally first. It is NOT scripts/check_all.sh (35 minutes,
# every shipped check): this is a fixed, small, fast slice picked for signal
# per dollar of wall clock, so nobody skips it under deadline pressure.
#
# Same three rules as check_all.sh, because this is a smaller instance of the
# same gate, not a different design: (1) each check's own exit code, captured
# before anything else touches $?; (2) name what failed, with its full output
# saved to a file the summary names; (3) NO-DATA (exit 2) is reported and
# never counted as a pass.
#
# Exit 0 when every check passes or reports NO-DATA. Exit 1 if any check
# FAILS. Target: well under 5 minutes wall clock (measured ~90s on this
# machine, 2026-09-03).

cd "$(dirname "$0")/.." || exit 1

# E100. Two worktrees run this gate at the same time during a night run and
# they share one $TMPDIR. Lane BM2's run once read a traceback out of lane
# AW2's tree because the failure capture was keyed by check name alone. The
# key is now the WORKTREE plus the pid, so two lanes can neither write nor
# read each other's file.
worktree_key="$(basename "$(pwd)")-$$"

pass=0; fail=0; nodata=0
failed_names=""
nodata_names=""
summary_printed=0

# A gate that dies before its own summary (a killed lane, a full disk, a
# peer's pkill) used to print nothing at all, which reads exactly like a
# clean tail to whoever scrolls to the end. NO-DATA is never a pass, so say
# so on the way out.
early_exit_report() {
  [ "$summary_printed" -eq 1 ] && return 0
  echo
  echo "NO-DATA: required-fast stopped after $((pass+fail+nodata)) check(s), before its own summary (worktree $worktree_key).  This is NOT a pass."
}
trap early_exit_report EXIT

run_check() {
  name="$1"; shift
  start="$(date +%s)"
  out="$("$@" 2>&1)"
  code=$?                      # the COMMAND's code, captured before anything else
  elapsed="$(($(date +%s) - start))"
  last="$(printf '%s\n' "$out" | tail -1 | cut -c1-72)"
  keep=""
  case "$code" in
    0) pass=$((pass+1));   verdict="PASS   " ;;
    2) nodata=$((nodata+1)); verdict="NO-DATA"; nodata_names="$nodata_names $name" ;;
    *) fail=$((fail+1));   verdict="FAIL   "; failed_names="$failed_names $name"
       # CAPTURE EVERYTHING, READ A SLICE (same estate lesson as check_all.sh).
       keep="${TMPDIR:-/tmp}/required-fast-fail-$name-$worktree_key.txt"
       printf '%s\n' "$out" > "$keep" 2>/dev/null && last="$last  [full: $keep]"
       ;;
  esac
  printf '%-7s exit %-3s %-20s %4ss  %s\n' "$verdict" "$code" "$name" "$elapsed" "$last"
  # VISIBILITY ON A RUNNER: $keep lives under /tmp, which a GitHub runner
  # discards with the job, so a failure's full output was NO-DATA the one
  # place it mattered. On REQUIRED_FAST_PRINT_FAILURES=1 or GITHUB_ACTIONS,
  # print the saved file's tail right after the FAIL line, instead of only
  # naming a path nobody downstream can open. Exit codes are untouched.
  if [ -n "$keep" ] && [ -f "$keep" ] \
     && { [ "$REQUIRED_FAST_PRINT_FAILURES" = "1" ] || [ -n "$GITHUB_ACTIONS" ]; }; then
    echo "---- $name failure detail ----"
    tail -80 "$keep"
    echo "---- $name failure detail ----"
  fi
}

echo "Brother: required-fast, the pre-merge contract"
echo

run_check "version-truth"       python3 scripts/test_version_truth.py
run_check "bundle-runtime"      python3 scripts/test_bundle_runtime.py -v
run_check "surface"             /usr/bin/python3 -m unittest tests/test_surface.py
run_check "brother-run"         python3 scripts/test_brother_run.py -v
run_check "integrate"           python3 scripts/test_integrate.py -v
run_check "worktree-lane"       python3 scripts/test_worktree_lane.py -v
run_check "receipt-door"        python3 scripts/test_receipt_door.py -v
run_check "receipt-contract-v1"  python3 scripts/test_receipt_contract.py -v
# Every persona pack, generically (enumerates scripts/packs, runs each
# pack's own detection fixture through the real inference). 0.9s wall on
# this machine, so it belongs in the fast slice.
run_check "packs"               python3 scripts/test_packs.py -v
# NO-DATA semantics: battery_verdict.py's own contract is that NO-DATA is
# never a pass, driven backwards over fixture check_all outputs. Dropped
# products/brothersbe/evals/test_no_data_class.py for this lane: it took
# over 120 seconds on this machine, well past this script's own budget.
run_check "no-data-semantics"   python3 scripts/test_battery_verdict.py -v
# Docs and runtime drift guard: SYSTEM.md is generated from the code and
# --check refuses a stale copy, closing team complaint P12 (a design doc
# that quietly went wrong and nobody could tell).
run_check "docs-runtime-drift"  python3 scripts/system_doc.py --check
# BO2: the README's own executable claims. Sub-second, and the front page is
# the first thing an outside reader runs, so it earns a place in the cheap
# slice rather than only in the full battery.
run_check "readme-honesty"      python3 scripts/test_readme_honesty.py
# E47: the charter named an architecture of record the tree did not hold,
# and tests/test_surface.py could not notice because it matched the
# record's NAME as a string in COORDINATION.md. This opens every path the
# charter names. 0.09s on this machine, so it belongs in the fast slice.
run_check "charter-paths"       python3 scripts/charter_paths.py
run_check "export-public"       python3 scripts/test_export_public.py -v
# The gate the 2026-09-05 v1.0.6 defect proved was missing: every other
# check in this file (and codex_smoke.py, in the full battery) ran from a
# tree where loop_bridge.py's own development fallback was reachable, so a
# public export that shipped bundle/runtime/loop_bridge.py without the
# modules it imports still read green everywhere. This builds the export
# the documented way, carves out ONLY bundle/ (what a plugin install
# actually receives), makes the development fallback unreachable, and
# proves one unit closes through the exported engine alone. Measured
# 2026-09-05: ~24s wall on this machine, well inside this file's own
# budget.
run_check "virgin-unit-proof"   python3 scripts/virgin_unit_proof.py
# The enterprise readiness gate itself, not only its self-test: the public
# v1.0.0 tag failed this gate the night the battery still read green,
# because only readiness-gate-self (the suite) was registered anywhere.
run_check "readiness-gate"      python3 scripts/readiness_gate.py

# THE KEY COMPONENTS, every one of them, 2026-09-10. The law of 2026-09-10
# says a component that must gate merges declares ci_gated and its guard goes
# in THIS set. All eight declared it and none was here, so the clause that
# enforces it passed vacuously over all eight while their guards sat in a
# 264 check battery no workflow runs. Measured before adding: the eight
# together cost about nine seconds, against a set that runs about sixteen
# minutes, so the objection that the fast gate cannot afford them was false.
run_check "key-components"      python3 scripts/key_components.py
run_check "intake-screen"       python3 scripts/test_decide.py
run_check "readiness-board"     python3 scripts/test_gen_readiness_board.py
run_check "board-status"        python3 scripts/test_board_status.py
run_check "daybook"             python3 scripts/test_daybook.py
run_check "handover-ceremony"   python3 scripts/test_handover_ceremony.py
run_check "closing-ceremony"    python3 scripts/test_close_ceremony_check.py
run_check "doc-assurance"       python3 scripts/doc_assurance.py --selftest
run_check "system-inventory"    python3 scripts/test_system_doc.py
if command -v claude >/dev/null 2>&1; then
  run_check "plugin-manifest"   claude plugin validate .
else
  run_check "plugin-manifest"   sh -c 'echo "NO-DATA: claude binary not found on PATH"; exit 2'
fi

echo
echo "pass $pass   fail $fail   no-data $nodata"
[ -n "$failed_names" ] && echo "FAILED:$failed_names"
[ -n "$nodata_names" ] && echo "NO-DATA:$nodata_names  (not a pass, and not a failure)"
summary_printed=1
[ "$fail" -eq 0 ] || exit 1
exit 0
