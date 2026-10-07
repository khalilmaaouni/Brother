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
# Exit 0 when no check fails and every missing result is allowed by its
# evidence obligation. Required missing evidence blocks without changing its verdict. Target: well under 5 minutes wall clock (measured ~90s on this
# machine, 2026-09-03).

cd "$(dirname "$0")/.." || exit 1

# ACC2: the audited checks below run REQUIRED_FAST_JOBS at a time, four by
# default since the closing check scripts/donecheck_acc2.py passed (side by
# side under half the one-at-a-time wall, every verdict identical; the run is
# quoted in the ACC2 plan row). REQUIRED_FAST_JOBS=1 restores the original
# one-at-a-time order. A caller that names shared state (a Jev, config or Codex
# directory, a reservation path or a refusal log) gets 1, because checks side by
# side would share it.
jobs=${REQUIRED_FAST_JOBS:-4}
case "$jobs" in
  ''|0*|*[!0-9]*) echo "NO-DATA: REQUIRED_FAST_JOBS must be a positive integer" >&2; exit 2 ;;
esac
# The variables that force one at a time are NAMED, so the width line below can say why (2026-10-04).
forced_by=""
for v in BROTHER_JEV_STATE_DIR BROTHER_CONFIG_DIR CLAUDE_CONFIG_DIR CODEX_HOME BROTHER_MACHINE_RESERVATION_PATH BROTHER_LOAD_REFUSALS_LOG; do
  eval "val=\${$v:-}"
  [ -z "$val" ] || forced_by="$forced_by $v"
done
if [ -n "$forced_by" ]; then
  jobs=1
fi
# C0.4b: a pinned order (REQUIRED_FAST_ORDER_PIN) is an exact sequence, so it
# runs one check at a time.
[ -z "${REQUIRED_FAST_ORDER_PIN:-}" ] || { jobs=1; forced_by="$forced_by REQUIRED_FAST_ORDER_PIN"; }

# Heavy work waits its turn (scripts/heavy_slot.py): on 2026-09-26 every
# session's gates ran on top of each other and every check here was 3 to 9
# times slower than alone. Re-enter this gate holding one shared machine slot.
# A fixture copy without heavy_slot.py beside it, a nested run, or
# BROTHER_HEAVY_SLOT=off runs straight through.
if [ -z "${BROTHER_HEAVY_SLOT_HELD:-}" ] && [ "${BROTHER_HEAVY_SLOT:-on}" != "off" ] \
   && [ -f scripts/heavy_slot.py ]; then
  BROTHER_HEAVY_SLOT_WEIGHT=$jobs; export BROTHER_HEAVY_SLOT_WEIGHT
  exec python3 scripts/heavy_slot.py sh "scripts/required_fast.sh" "$@"
fi

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
# Every check's name and exit code, for the evidence obligation step at the end
# (scripts/gate_obligations.json): a NO-DATA the map does not explain blocks.
codes_file="${TMPDIR:-/tmp}/required-fast-codes-$worktree_key.txt"
: > "$codes_file" || exit 1

# D4.d REQ-GL. The durable gate ledger, added BESIDE codes_file rather than
# instead of it: codes_file keeps the exact two field `name TAB code` shape
# the obligation step reads, and this new file carries the third field
# (milliseconds) the durable record needs. It lives under the run directory
# when one is exported and falls back to $TMPDIR, is truncated once at
# startup and never deleted, so a restart in the same run directory appends a
# new pid keyed file and leaves the prior one alone.
gate_ledger_dir="${BROTHER_RUN_DIR:-${TMPDIR:-/tmp}}"
gate_ledger="$gate_ledger_dir/required-fast-gates-$worktree_key.tsv"
if [ -d "$gate_ledger_dir" ] && [ -w "$gate_ledger_dir" ]; then
  : > "$gate_ledger"
else
  # NO-DATA, never a pass: with nowhere durable to write, the append below is
  # skipped and named, and the check verdict is untouched.
  echo "NO-DATA: gate ledger directory $gate_ledger_dir is not writable; the durable gate ledger carries nothing this run" >&2
  gate_ledger=""
fi

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

# Each check running side by side writes its own result files here; only
# this process adds them up, so no two writers share a counter or a file.
pool="$(mktemp -d "${TMPDIR:-/tmp}/required-fast-pool.XXXXXX")" || exit 1
pending=""
running=0

# Every check below runs against a scratch Jev state directory. With seams in
# shadow, suites that exercise a seam call site consult for real, and their
# rows used to land in the live calibration ledger (~/.brother/jev): measured
# 2026-09-20, 397 ungraded rows in one hour from gate and test runs alone.
# A caller that set its own directory keeps it.
if [ -z "${BROTHER_JEV_STATE_DIR:-}" ]; then
  BROTHER_JEV_STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/required-fast-jev.XXXXXX")" || exit 2
  export BROTHER_JEV_STATE_DIR
fi

# A battery launched from inside a git hook inherits GIT_DIR, and then every
# test that runs `git init`, `git config` or `git commit` in a temp repository
# writes into the REAL one (measured 2026-09-20: core.bare, core.hooksPath and
# a fixture identity landed in the estate's shared config). cwd decides the
# repository from here on. The list is tmp_sandbox.GIT_LOCATION_VARS, pinned
# by test_hermetic_test_check.py.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_PREFIX GIT_NAMESPACE GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE GIT_SHALLOW_FILE GIT_INTERNAL_SUPER_PREFIX GIT_REPLACE_REF_BASE GIT_NO_REPLACE_OBJECTS
python3 scripts/git_location_guard.py --assert-clean --where required_fast || exit 2

# D4.d REQ-GL. Milliseconds for shell timing. POSIX sh carries no monotonic
# clock, so this reads the wall clock at whatever resolution the platform
# offers; the clamp in run_check, never the verdict, absorbs a backward step.
now_ms() {
  now_ms_s="$(date +%s)"
  now_ms_ns="$(date +%s%N 2>/dev/null)"
  case "$now_ms_ns" in
    *%N*) now_ms_ns="" ;;
  esac
  if [ -n "$now_ms_ns" ]; then
    printf '%s\n' "$(( now_ms_ns / 1000000 ))"
  else
    printf '%s\n' "$(( now_ms_s * 1000 ))"
  fi
}

# C0.4b: the order pin. Unset, nothing below changes today's behaviour: the
# pin apparatus is inert and every check runs exactly as it always has. Set,
# this run is validated against the pin (the pin's gate_script_sha256 must be
# this file's own digest and its mandatory_set must equal the run_check names
# this run itself declares) and the checks run in the pin's order instead of
# the declared one. A pin that cannot be read, a hash mismatch or a set
# mismatch refuses with exit 2: NO-DATA, never a silent fallback to the
# declared order and never a pass.
order_pin="${REQUIRED_FAST_ORDER_PIN:-}"
order_dir=""
order_defer=""
if [ -n "$order_pin" ]; then
  order_dir="$(mktemp -d "${TMPDIR:-/tmp}/required-fast-pin.XXXXXX" 2>/dev/null)"
  if [ -z "$order_dir" ] || [ ! -d "$order_dir" ]; then
    summary_printed=1
    echo "NO-DATA: REQUIRED_FAST_ORDER_PIN is set but no scratch directory could be made; the pin was NOT applied and no check ran" >&2
    exit 2
  fi
  order_seen="$order_dir/declared.names"
  : > "$order_seen"
  order_defer=1
fi

check_now() {
  name="$1"; shift
  if [ -n "$order_defer" ]; then
    case "$name" in
      ""|.*|-*|*[!A-Za-z0-9._-]*)
        summary_printed=1
        echo "NO-DATA: run_check name '$name' cannot be a pin scratch file name; the pinned order is refused" >&2
        exit 2
        ;;
    esac
    printf '%s\n' "$name" >> "$order_seen"
    : > "$order_dir/$name.argv"
    for order_arg in "$@"; do
      printf '%s\n' "$order_arg" >> "$order_dir/$name.argv"
    done
    return 0
  fi
  start_ms="$(now_ms)"         # D4.d: captured BEFORE the command runs
  start_s="$(date +%s)"
  start_ns="$(date +%s%N 2>/dev/null)"
  case "$start_ns" in
    *%N*) start_ns="" ;;
  esac
  [ -n "$start_ns" ] || start_ns=$((start_s * 1000000000))
  out="$("$@" 2>&1)"
  code=$?                      # the COMMAND's code, captured before anything else
  printf '%s\t%s\n' "$name" "$code" >> "$codes_file" || exit 1
  # D4.d REQ-GL: the durable three field ledger line, appended BESIDE the two
  # field obligation input above. A clock that steps back yields a negative
  # delta; the clamp holds the duration at zero or more and never touches the
  # verdict, which was captured before this point.
  end_ms="$(now_ms)"
  duration_ms=$(( end_ms - start_ms ))
  [ "$duration_ms" -ge 0 ] || duration_ms=0
  if [ -n "$gate_ledger" ]; then
    printf '%s\t%s\t%s\n' "$name" "$code" "$duration_ms" >> "$gate_ledger"
  fi
  end_ns="$(date +%s%N 2>/dev/null)"
  case "$end_ns" in
    *%N*) end_ns="" ;;
  esac
  if [ -z "$end_ns" ]; then
    end_s="$(date +%s)"
    end_ns=$((end_s * 1000000000))
  fi
  elapsed=$(( (end_ns - start_ns) / 1000000000 ))
  duration_s="$(python3 -c 'import sys; print("%.6f" % ((int(sys.argv[2])-int(sys.argv[1]))/1000000000.0))' "$start_ns" "$end_ns" 2>/dev/null || echo "$elapsed.0")"
  last="$(printf '%s\n' "$out" | tail -1 | cut -c1-72)"
  keep=""
  # C0.3 ledger append: facts are written where they exist. A missing or
  # corrupt context refuses the append, never the check verdict. Its stderr
  # (the "NO-DATA: gate ledger append refused" line) used to go to /dev/null
  # (2026-09-30), so the refusal reached nobody: it now rides on this check's
  # summary line. The check's own exit code is untouched either way.
  ledger_note="$(WORKTREE_KEY="$worktree_key" python3 - "$name" "$code" "$start_ns" "$end_ns" "$duration_s" <<'PY' 2>&1 >/dev/null
import json, os, sys
sys.path.insert(0, "scripts")
import gate_ledger
name, code, start_ns, end_ns, duration_s = sys.argv[1:6]
record = {
    "schema": 1,
    "cut_id": os.environ.get("CUT_ID", ""),
    "cut_version": os.environ.get("CUT_VERSION", "UNKNOWN"),
    "gate_name": name,
    "gate_argv": [],
    "status": "PASS" if int(code) == 0 else ("NO-DATA" if int(code) == 2 else "FAIL"),
    "exit_code": int(code),
    "started_unix": float(start_ns) / 1000000000.0,
    "ended_unix": float(end_ns) / 1000000000.0,
    "duration_s": float(duration_s),
    "worktree_key": os.environ.get("WORKTREE_KEY", "UNKNOWN"),
    "tree_sha": os.environ.get("TREE_SHA", "UNKNOWN"),
    "inputs_hash": None,
    "code_hash": os.environ.get("GATE_SCRIPT_SHA256", "0"*64),
    "tool_versions": {},
    "verdict_source": "full",
    "cache_key": None,
    "deadline_unix": None,
}
try:
    gate_ledger.append_ledger(os.environ.get("GATE_LEDGER_PATH", "logs/gate-ledger.jsonl"), record)
except Exception as exc:
    sys.stderr.write("NO-DATA: gate ledger append refused: %s\n" % exc)
PY
)" || true
  if [ -n "$ledger_note" ]; then
    last="$last  [$(printf '%s\n' "$ledger_note" | tail -n 1 | cut -c1-160)]"
  fi
  case "$code" in
    0) pass=$((pass+1));   verdict="PASS   "
       # L4b.1 RQ-RF-PASS-KEEP: a PASS check's own combined stdout plus
       # stderr is durable too, and the summary line names the handle, so a
       # session or subagent reads a passing check's full output from the
       # handle instead of rerunning the check. The verdict above is already
       # captured, so a keep that cannot be written loses only the handle,
       # never the PASS. The handle is named, never printed.
       pass_keep="${TMPDIR:-/tmp}/required-fast-pass-$name-$worktree_key.txt"
       if printf '%s' "$out" > "$pass_keep"; then
         last="$last  [full: $pass_keep]"
       else
         echo "NO-DATA: PASS keep for $name was refused at $pass_keep; the check verdict is unchanged" >&2
       fi
       ;;
    2) nodata=$((nodata+1)); verdict="NO-DATA"; nodata_names="$nodata_names $name" ;;
    *) fail=$((fail+1));   verdict="FAIL   "; failed_names="$failed_names $name"
       # CAPTURE EVERYTHING, READ A SLICE (same estate lesson as check_all.sh).
       keep="${TMPDIR:-/tmp}/required-fast-fail-$name-$worktree_key.txt"
       printf '%s\n' "$out" > "$keep" || exit 1
       last="$last  [full: $keep]"
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

# Collect a finished check (any one, not only the oldest) and add up its result.
reap_one() {
  while :; do
    for entry in $pending; do
      child_name=${entry%:*}
      child_pid=${entry##*:}
      if [ -f "$pool/$child_name.done" ] || ! kill -0 "$child_pid" 2>/dev/null; then
        wait "$child_pid"
        worker_code=$?
        if [ "$worker_code" -ne 0 ] ||
           ! read -r saved_name saved_code extra < "$pool/$child_name.code" ||
           [ "$saved_name" != "$child_name" ] || [ -n "$extra" ]; then
          echo "NO-DATA: incomplete $child_name result; output: $pool/$child_name.log" >&2
          exit 1
        fi
        case "$saved_code" in
          ''|*[!0-9]*) echo "NO-DATA: invalid result for $child_name" >&2; exit 1 ;;
        esac
        cat "$pool/$child_name.log" || exit 1
        printf '%s\t%s\n' "$saved_name" "$saved_code" >> "$codes_file" || exit 1
        case "$saved_code" in
          0) pass=$((pass+1)) ;;
          2) nodata=$((nodata+1)); nodata_names="$nodata_names $saved_name" ;;
          *) fail=$((fail+1)); failed_names="$failed_names $saved_name" ;;
        esac
        remaining=""
        for other in $pending; do
          [ "$other" = "$entry" ] || remaining="$remaining $other"
        done
        pending=$remaining
        running=$((running-1))
        return
      fi
    done
    sleep 1
  done
}

drain_checks() {
  while [ "$running" -gt 0 ]; do reap_one; done
}

# Two passes over the list below: the audited checks start side by side in the
# first, everything else runs one at a time in the second, after the first has
# drained. A check nobody audited for shared state is serial by default.
# loop-wiring joined 2026-09-26: it only reads scripts/loop and the installed
# copies in ~/.claude/bin and makes one private temp folder (24 s alone).
run_check() {
  if [ "$jobs" -eq 1 ]; then
    [ "$phase" != parallel ] || check_now "$@"
    return
  fi
  case "$1" in
    version-truth|bundle-runtime|native-evidence|mobile-workflow|\
    mobile-design|surface|brother-run|brother-run-2|worktree-lane|\
    packs|no-data-semantics|no-data-class-lints|receipt-door|receipt-contract-v1|\
    brothermode-verify-install|brothersbe-verify-install|readme-honesty|charter-paths|\
    export-public|export-public-2|release-cut|cursor-plugin-self|cursor-hook-run-self|\
    client-parity|readiness-gate|key-components|\
    intake-screen|board-status|handover-ceremony|closing-ceremony|\
    closing-ceremony-real|doc-assurance|system-inventory|evidence-obligation|\
    loop-wiring) check_phase=parallel ;;
    *) check_phase=serial ;;
  esac
  [ "$phase" = "$check_phase" ] || return 0
  if [ "$phase" = serial ]; then
    check_now "$@"
    return
  fi
  while [ "$running" -ge "$jobs" ]; do reap_one; done
  (
    trap - 0
    codes_file="$pool/$1.code"
    BROTHER_JEV_STATE_DIR="$pool/$1.jev"
    export BROTHER_JEV_STATE_DIR
    # ACC2.4: the engine's machine reservation and load refusal log, private
    # to this check, so a suite that launches brother_run.py never shares them.
    BROTHER_MACHINE_RESERVATION_PATH="$pool/$1.reservation.json"
    BROTHER_LOAD_REFUSALS_LOG="$pool/$1.refusals.jsonl"
    export BROTHER_MACHINE_RESERVATION_PATH BROTHER_LOAD_REFUSALS_LOG
    mkdir "$BROTHER_JEV_STATE_DIR" || exit 1
    check_now "$@" || exit 1
    : > "$pool/$1.done"
  ) > "$pool/$1.log" 2>&1 &
  pending="$pending $1:$!"
  running=$((running+1))
}

echo "Brother: required-fast, the pre-merge contract"
echo

for phase in parallel serial; do
[ "$phase" != serial ] || drain_checks

run_check "version-truth"       python3 scripts/test_version_truth.py
# Longest first (ACC2, measured 2026-09-26): these two take about 115 s each
# alone, so they start at once instead of 7th and 20th; otherwise the pool
# ends on one long check running by itself. version-truth stays first: it
# is the anchor test_required_fast.py cuts the check list on, and takes 0 s.
run_check "brother-run"         env BROTHER_TEST_SHARD=1/2 python3 scripts/test_brother_run.py -v
run_check "export-public"       env BROTHER_TEST_SHARD=1/2 python3 scripts/test_export_public.py -v
run_check "brother-run-2"       env BROTHER_TEST_SHARD=2/2 python3 scripts/test_brother_run.py -v
run_check "export-public-2"     env BROTHER_TEST_SHARD=2/2 python3 scripts/test_export_public.py -v
run_check "supply-chain-gate"   python3 scripts/supply_chain_gate.py --offline --repo .
run_check "bundle-runtime"      python3 scripts/test_bundle_runtime.py -v
run_check "native-evidence"     python3 scripts/test_native_evidence.py -v
run_check "mobile-workflow" python3 scripts/test_mobile_workflow.py -v
run_check "mobile-design" python3 scripts/test_mobile_design.py -v
run_check "surface"             /usr/bin/python3 -m unittest tests/test_surface.py
run_check "bm-clock-guard"      python3 -B -m unittest products.brothermode.tools.test_bm_clock_guard products.brothermode.tools.test_bm_clock_guard_m12 products.brothermode.tools.test_bm_clock_guard_m13 products.brothermode.tools.test_bm_clock_guard_m14
# L2b: plugin/runtime/brother's tests, absent from every gate until now
# (docs/architecture/ONE-SYSTEM-WIRING-AUDIT.md). The 3 known real
# multiprocess-fork files run isolated, in the full battery only; see
# scripts/plugin_runtime_fast_discover.py for exactly which 3 and why.
# Those tests stay in the private hub: the public export carries the runtime
# modules the loop imports and none of their tests (owner ruling 2026-09-26,
# docs/plan/EXPORT-ALLOWLIST.txt), and the public repository's CI and the cut
# preflight's export tree gate run this same file on that tree. Reached for
# the first time on 2026-10-06, this row read FAIL there ("no test modules
# found"): a red for an input the tree is not meant to hold. A tree that is
# not the hub (no .brother-edition, which no export ever carries) and holds no
# test module reports NO-DATA, as the readiness board does below. The hub
# always runs the discoverer, so the hub losing these tests still FAILS, and a
# tree that does carry one runs it whatever its edition.
if [ -e .brother-edition ] || [ -n "$(find plugin/runtime/brother -name 'test_*.py' 2>/dev/null | head -n 1)" ]; then
  run_check "plugin-runtime-tests-fast" python3 scripts/plugin_runtime_fast_discover.py
else
  run_check "plugin-runtime-tests-fast" sh -c 'echo "NO-DATA: this tree carries no plugin/runtime/brother test module: the public export ships the runtime modules without their tests, which stay in the private hub"; exit 2'
fi
run_check "integrate"           python3 scripts/test_integrate.py -v
run_check "worktree-lane"       python3 scripts/test_worktree_lane.py -v
run_check "receipt-door"        python3 scripts/test_receipt_door.py -v
run_check "receipt-contract-v1"  python3 scripts/test_receipt_contract.py -v
# E-C2: proof_card.py is now wired into brother_run.py's main() (it used to
# pass its own selftest, registered in check_all.sh, with no caller at all),
# so a defect in it is a defect in every run's own delivered output, not
# just in the standalone tool. 0.02s wall: belongs in the fast slice.
run_check "proof-card-self"     python3 scripts/test_proof_card.py -v
# Every persona pack, generically (enumerates scripts/packs, runs each
# pack's own detection fixture through the real inference). 0.9s wall on
# this machine, so it belongs in the fast slice.
run_check "packs"               python3 scripts/test_packs.py -v
# NO-DATA semantics: battery_verdict.py's own contract is that NO-DATA is
# never a pass, driven backwards over fixture check_all outputs. The FULL
# products/brothersbe/evals/test_no_data_class.py stays out of this lane: its
# scenario sweep (about 4,300 scenarios) took over 120 seconds on this
# machine, well past this script's own budget, and check_all.sh runs it.
run_check "no-data-semantics"   python3 scripts/test_battery_verdict.py -v
# Its static half runs here. Dropping the whole file left its two source
# lints (a PASS-capable pair returner outside every registry, a report print
# that skips say()) looked at by nothing per PR, and six reds sat on main for
# two weeks (2026-09-12 to 2026-09-26). --lint-only runs registry discovery,
# both lints and both allowlists' reconciliation through the same function
# the full run uses, and says the sweep did not run. cd matches the product
# battery (release-control/baseline/run-battery.sh) and brothersbe-gates.yml.
run_check "no-data-class-lints" sh -c 'cd products/brothersbe && python3 evals/test_no_data_class.py --lint-only'
# Docs and runtime drift guard: SYSTEM.md is generated from the code and
# --check refuses a stale copy, closing team complaint P12 (a design doc
# that quietly went wrong and nobody could tell).
# docs-runtime-drift left this fast set on 2026-09-26 (ACC2): system-inventory
# already asserts system_doc.main(["--check"]) == 0 (test_system_doc.py).
# Same class of gap by another mechanism: hub main shipped a stale
# products/brothermode/CHECKSUMS.sha256 after tools/bm_stall.py changed
# without regenerating it, and nothing in this gate noticed. Each product's
# own installer verifier is the check a real install would run; wiring it
# here closes that hole for both products it covers.
run_check "brothermode-verify-install" sh -c 'cd products/brothermode && sh scripts/verify-install.sh'
run_check "brothersbe-verify-install"  sh -c 'cd products/brothersbe && sh scripts/verify-install.sh'
# BO2: the README's own executable claims. Sub-second, and the front page is
# the first thing an outside reader runs, so it earns a place in the cheap
# slice rather than only in the full battery.
run_check "readme-honesty"      python3 scripts/test_readme_honesty.py
# E47: the charter named an architecture of record the tree did not hold,
# and tests/test_surface.py could not notice because it matched the
# record's NAME as a string in COORDINATION.md. This opens every path the
# charter names. 0.09s on this machine, so it belongs in the fast slice.
run_check "charter-paths"       python3 scripts/charter_paths.py
# The release cut orchestrator. Registered 2026-09-17 after three real-store
# defects stopped the 1.0.19 cut one attempt at a time (park-only precedence,
# refusing its own fence, releasing under a different session): its tests
# existed but gated nothing. Includes real temporary-store lifecycle tests
# (claim, precedence, decline, gate failure, release). About 6 s here.
run_check "release-cut"         python3 scripts/test_cut.py
# The release cut's plugin bump gate: a plugin whose shipped files changed
# must carry a new version, because `claude plugin update` compares the
# version string only. Registered 2026-09-20 after 1.0.19 and 1.0.21 shipped
# changed product files under unchanged versions and installed machines kept
# the old code. Real temporary git repositories, no network. About 12 s here.
run_check "plugin-bump-gate"    python3 scripts/test_plugin_bump_gate.py
# The Cursor package: the manifest, the Cursor marketplace, the hooks file
# routed through the Cursor adapter, the rules, and a local install into a
# temp dir. No binary is needed, so it earns the fast slice.
run_check "cursor-plugin-self"  python3 scripts/test_cursor_plugin.py
# The Cursor payload translation seam driven from both sides: a Cursor
# shell payload handed straight to a Claude-shaped guard is allowed, which
# is the defect, and the same payload through bm_cursor_hook.py --run is
# denied. No binary is needed, so it also earns the fast slice.
run_check "cursor-hook-run-self" python3 scripts/test_cursor_hook_run.py -v
# Founder order 2026-09-12: Cursor stays at parity with Codex at every
# release. Every tracked Codex surface and every codex- battery check needs
# a Cursor twin, an exemption or a dated debt, and a debt past its release
# fails. Here, not only in the full battery, so a pull request that adds a
# Codex surface alone is refused before merge.
run_check "client-parity"       python3 scripts/test_client_parity.py -v
# cursor-smoke is deliberately NOT in this gate. It needs the cursor-agent
# binary, which CI does not have, so on every CI run it would only ever
# read NO-DATA (exit 2) and prove nothing. The full battery owns it, and
# docs/cursor/SMOKE-RUNBOOK.md closes the signed-in half by hand.
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
# virgin-unit-proof left this fast set on 2026-09-26 (ACC5): with the Codex
# binary found again it builds the whole export and drives a real install on
# every run. Its obligation was always REQUIRED_FOR_RELEASE; the release
# closeout and check_all.sh run it for real.
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
# The readiness board and board status read the private roadmap, which the
# public export never ships (the public repository's CI runs this same file
# on the export tree). Absent there, they report NO-DATA, as plugin-manifest
# does below, rather than a failure the tree cannot fix.
if [ -f docs/plan/READINESS-ROADMAP-2026-08-29.json ]; then
  run_check "readiness-board"   python3 scripts/test_gen_readiness_board.py
  run_check "board-status"      python3 scripts/test_board_status.py
else
  run_check "readiness-board"   sh -c 'echo "NO-DATA: this tree carries no docs/plan/READINESS-ROADMAP-2026-08-29.json, a private hub file the public export never ships"; exit 2'
  run_check "board-status"      sh -c 'echo "NO-DATA: this tree carries no docs/plan/READINESS-ROADMAP-2026-08-29.json, a private hub file the public export never ships"; exit 2'
fi
run_check "daybook"             python3 scripts/test_daybook.py
run_check "handover-ceremony"   python3 scripts/test_handover_ceremony.py
run_check "closing-ceremony"    python3 scripts/test_close_ceremony_check.py
# F2 (root-cause fix, 2026-09-14 adversarial sweep): the FIXTURE suite above
# only proves close_ceremony_check.py's own logic against hand-built zips; a
# real handover pack's zip shape (member paths prefixed with the pack's own
# directory name) was different enough that the gate FAILED on every real
# pack while its fixture suite stayed green. Same lesson this file already
# names for readiness-gate (a tag failed on the real gate while only its
# self-test ran): a gate's fixture suite is never a substitute for running
# the gate. Reads the machine's own handover root
# (~/Documents/BrotherModeUp-handovers), which CI does not have, so it is
# NO-DATA there by the gate's own exit-2 convention; the local lander and
# the release closeout are where this runs for real.
run_check "closing-ceremony-real" python3 scripts/close_ceremony_check.py
run_check "doc-assurance"       python3 scripts/doc_assurance.py --selftest
run_check "system-inventory"    python3 scripts/test_system_doc.py

# The torture demo's honesty property: it may never say PASS while one of
# its three behaviours did not run. 0.4s wall on this machine. In the cheap
# slice and not only the full battery for this estate's own recorded reason
# (registration is not protection): the demo is what an outsider is shown,
# and a guard that only runs in the 35 minute battery can be red for weeks
# behind green pull requests.
run_check "torture-demo-self"   python3 scripts/test_demo_torture.py -v
run_check "evidence-obligation" python3 scripts/test_evidence_obligation.py
# The detective half of the git hook leak fix (2026-09-20): fails when the
# SHARED repository config carries what a leaked test fixture leaves behind
# (core.bare=true, a core.hooksPath that does not exist, a fixture identity).
run_check "shared-config"       python3 scripts/shared_config_check.py .

# R2.6 (R20): the shared repository config detective, asked once per fast run
# against the captured repository root (R12: this file cd's to the root once
# and never leaves it) rather than the tool's own default.
run_check "git-location-config" python3 scripts/git_location_guard.py --check-live-config --repo-root "$(pwd)"

# brother.loop, unit BL. These are in the FAST gate because KEY-COMPONENTS.json declares each of
# them ci_gated, and key_components.py refuses that claim unless this file actually runs the guard:
# a component declared gated whose guard no gate runs lets a pull request go green while the
# component is broken. Every one of these was timed before it was added here, and the slowest is
# 779ms, so the gate's budget is not the reason to leave any of them out.
run_check "loop-ownership-race"  python3 scripts/test_loop_guard_race.py
run_check "loop-health"          python3 scripts/loop/loop_heartbeat.py --selftest
run_check "loop-lifecycle"       python3 scripts/test_loop_lifecycle_probes.py
run_check "loop-reporting"       python3 scripts/loop/loop_report.py --selftest
run_check "loop-model-routing"   python3 scripts/loop/model_router.py --selftest
run_check "loop-model-call"      python3 scripts/loop/model_call.py --selftest
run_check "loop-tool-parity"     python3 scripts/test_loop_tool_parity.py
run_check "loop-owns-complete"   python3 scripts/test_bl_owns_complete.py
run_check "loop-silent-failure"  python3 scripts/test_loop_report_run.py Lint
run_check "loop-wiring"          python3 scripts/test_loop_wiring.py --max 4
# The one plugin submission readiness, static bars only (docs/plan/specs/OP1.md
# 5.5, OP1.f): no host binary, about 2 s. Exit 0 only when all six bars PASS,
# 1 on any FAIL, 2 when a bar is NO-DATA or missing. Until the 1.1.0 cut
# applies scripts/retire_catalogs.py the catalogs-one-plugin bar reads
# NO-DATA (awaiting the cut, spec R20 and decision 9), so this row reads
# NO-DATA here and the merge stage allows it through its gate_obligations.json
# entry (REQUIRED_FOR_RELEASE); the release stage blocks on it until the cut.
# Any FAIL is red in both stages, and a missing bar is never green.
run_check "one-plugin-readiness-static" python3 -B scripts/one_plugin_readiness.py --static
if command -v claude >/dev/null 2>&1; then
  run_check "plugin-manifest"   claude plugin validate .
else
  run_check "plugin-manifest"   sh -c 'echo "NO-DATA: claude binary not found on PATH"; exit 2'
fi

done
drain_checks
rm -rf "$pool"

# C0.4b: the deferred checks, replayed in the pin's order. The body above
# recorded each declared call instead of running it; the pin is verified
# against this very file and against the names this run declared before a
# single check is replayed, so a bad pin refuses with exit 2 and runs nothing.
if [ -n "$order_defer" ]; then
  order_defer=""
  python3 - "$order_pin" "$order_seen" "$order_dir/ordered.names" "$0" <<'PY'
import hashlib, json, sys

pin_path, seen_path, out_path, script_path = sys.argv[1:5]


def refuse(message):
    sys.stderr.write("NO-DATA: " + message + "\n")
    sys.exit(2)


try:
    with open(pin_path, "rb") as handle:
        pin_raw = handle.read()
except OSError as exc:
    refuse("REQUIRED_FAST_ORDER_PIN %s could not be read: %s" % (pin_path, exc))
try:
    pin = json.loads(pin_raw.decode("utf-8"))
except (ValueError, UnicodeDecodeError) as exc:
    refuse("REQUIRED_FAST_ORDER_PIN %s is not a JSON object: %s" % (pin_path, exc))
if not isinstance(pin, dict):
    refuse("REQUIRED_FAST_ORDER_PIN %s is not a JSON object" % pin_path)
try:
    with open(script_path, "rb") as handle:
        script_sha256 = hashlib.sha256(handle.read()).hexdigest()
except OSError as exc:
    refuse("this script could not be hashed: %s" % exc)
if pin.get("gate_script_sha256") != script_sha256:
    refuse("gate_script_sha256 %r does not match this script's own digest %s"
           % (pin.get("gate_script_sha256"), script_sha256))
try:
    with open(seen_path, "r", encoding="utf-8") as handle:
        declared = [line.strip() for line in handle if line.strip()]
except OSError as exc:
    refuse("the declared run_check names could not be read: %s" % exc)
mandatory = pin.get("mandatory_set")
order = pin.get("order")
if not isinstance(mandatory, list) or not mandatory:
    refuse("mandatory_set is missing or empty")
if not isinstance(order, list) or not order:
    refuse("order is missing or empty")
if len(set(declared)) != len(declared):
    refuse("this run declared a run_check name twice")
if set(mandatory) != set(declared):
    refuse("mandatory_set does not equal the run_check names this run declared")
if len(order) != len(declared) or set(order) != set(declared):
    refuse("order is not a permutation of the run_check names this run declared")
try:
    with open(out_path, "w", encoding="utf-8") as handle:
        for name in order:
            handle.write(name + "\n")
except OSError as exc:
    refuse("the pinned order could not be written: %s" % exc)
PY
  order_status=$?
  if [ "$order_status" -ne 0 ]; then
    summary_printed=1
    exit 2
  fi
  while IFS= read -r order_name; do
    [ -n "$order_name" ] || continue
    set --
    while IFS= read -r order_arg; do
      set -- "$@" "$order_arg"
    done < "$order_dir/$order_name.argv"
    check_now "$order_name" "$@"
  done < "$order_dir/ordered.names"
  rm -rf "$order_dir"
fi
# The width actually run, on a line of its own: the summary line below stays byte identical, because four readers
# anchor it at the end of the line (cut.py, required_fast_local.py, merge_gate.py, donecheck_L4b.py; review 2026-10-04).
echo "width $jobs${forced_by:+ (forced by$forced_by)}"
echo
echo "pass $pass   fail $fail   no-data $nodata"
[ -n "$failed_names" ] && echo "FAILED:$failed_names"
[ -n "$nodata_names" ] && echo "NO-DATA:$nodata_names  (not a pass, and not a failure)"
summary_printed=1
# The obligation step never rewrites a verdict: it decides whether this
# transition (a merge) may proceed. A NO-DATA the map explains is allowed
# with its reason printed; an unexplained one blocks. An obligation step that
# read nothing (exit 2) blocks too.
echo
python3 scripts/evidence_obligation.py transition --stage merge --repo . < "$codes_file"
obligation=$?
rm -f "$codes_file"
[ "$fail" -eq 0 ] || exit 1
[ "$obligation" -eq 0 ] || exit 1
exit 0
