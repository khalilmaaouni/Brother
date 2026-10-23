#!/bin/bash
# ONE REDEPLOY CEREMONY (M40, 2026-10-16). On 10-14 nine hand written redeployN.sh piled up in one night, and 12 of 19
# launches and 2 of 13 deploys were refused, each refusal arriving alone AFTER the loop was stopped (18 and 60 minutes
# lost). This file is the last of them (evidence/1.1.4-night/redeploy10.sh) made general, with a check mode that asks
# every refusal the deploy and the launcher would raise while the loop keeps running, and prints ALL of them.
#
# usage, from anywhere (the worktrees are named):
#   redeploy.sh --check     --plan-wt DIR --loop-wt DIR --plan-branch B --loop-branch B --deadline HH:MM
#                           [--probe-cap-usd USD]   (no cap: the seat row reads NO-DATA, so a launch refuses)
#   redeploy.sh             --plan-wt DIR --loop-wt DIR --plan-branch B --loop-branch B --deadline HH:MM
#                           --plan docs/plan/X.json --scope REGEX --budget-usd N [--reason TEXT] [--drain-s SECONDS]
#                           [--worker M] [--checker M] [--finisher M] [--documenter M] [--adversary M] [--gap SECONDS]
#                           [--lift-hold "<the owner's words>"]
#   redeploy.sh --plan-only --plan-wt DIR --loop-wt DIR --plan-branch B --loop-branch B [--reason TEXT] [--drain-s S]
# --deadline takes HH:MM (dated the way start_run.sh dates it) or "YYYY-MM-DD HH:MM".
# The launcher's own options (M40.c): --worker, --checker, --finisher, --documenter, --adversary, --gap and --lift-hold
#           are start_run.sh's, under its names and its value rule; each one given reaches start_run.sh verbatim and in
#           the order given, and one not given never does (start_run.sh keeps its own defaults and its own checks of
#           them). --check launches nothing: it takes them and prints one ROLES line naming them.
#
# --check   stops, drains and pushes NOTHING, and writes only inside one scratch folder it removes on exit (the merge
#           is computed with git merge-tree into a scratch object store, the remote is read with ls-remote, the seat
#           probe's folder sits in the scratch). One line per check, PASS, FAIL or NO-DATA, every check run even after
#           a FAIL: power, controls, live, plan tree, loop tree, merge, pins, seat, stage, parity, canary. Exit 0 when
#           all PASS, 1 on any FAIL, 2 when none failed but one could not run. The checks CALL the tools that refuse
#           (power_rule.py, loop_intake.seat_gate, deploy_stamped.stage_candidate and CONFIGS, tool_stamp.copy_tools,
#           scripts/test_loop_tool_parity.py, loop_canary's load_spec, missing_fixture_files and fixtures_of,
#           stop_loop.sh --dry); the tree lines read the same facts start_run.sh step 1 reads. The seat probe is one
#           real minimal call, recorded in the call ledger like the intake's. NOT run here: the staged canary replay
#           (minutes; the deploy runs it), the owns check on the merged tree (the redeploy runs it after the merge),
#           the intake's model proofs and budget rows.
# default   --check first, and a refusal before any drain. Then: drain, stop, resume (clears the drain's pause), merge
#           the loop line into the plan, scripts/test_bl_owns_complete.py, push the plan, fast forward and push the loop
#           line, the loop worktree's deploy_stamped.sh, the deployed stamp read back against the loop line's HEAD,
#           and the loop worktree's start_run.sh. A refusal prints "REDEPLOY REFUSED [<step>]: <why>", says what state
#           the loop is left in, and exits 2.
# --plan-only  for a change that touches only docs (plans, specs, notes): refuses BY NAME, before anything is paused,
#           any changed path outside docs/ and any docs/plan file the deploy itself ships (deploy_stamped.CONFIGS).
#           Then drain (a pass boundary: nothing is killed, the driver keeps its pid), merge, push both lines, resume.
# Seams for tests only: REDEPLOY_DEPLOY and REDEPLOY_START replace the loop worktree's deploy_stamped.sh and
# start_run.sh. Every other tool is the one beside this file.
set -u
set -o pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# LC3.a: the doctor check reads one verdict per capability from the enterprise doctor, on a certified host only.
DOCTOR="$HERE/../enterprise_doctor.py"
EXPECTED_CONTROLS="security-policy,code-owners,evidence-obligation,obligation-wired,signed-receipts,signing-identities,pinned-ci,ci-permissions,os-sandbox,egress-policy,delegated-identity,central-audit-store,fail-closed-mode"
HOST_SUPPORTED="Darwin"
die() { echo "REDEPLOY REFUSED [$1]: $2"; exit 2; }
MODE=run; PW=""; LW=""; PB=""; LB=""; PLAN=""; SCOPE=""; BUDGET=""; DEADLINE=""; REASON="redeploy"; DRAIN_S=3600; PROBE_CAP=""; ROLES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check; shift; continue;;
    --plan-only) MODE=planonly; shift; continue;;
    --plan-wt|--loop-wt|--plan-branch|--loop-branch|--plan|--scope|--budget-usd|--deadline|--reason|--drain-s|--probe-cap-usd|\
    --worker|--checker|--finisher|--documenter|--adversary|--gap|--lift-hold)
      [ $# -ge 2 ] || die args "$1 needs a value"
      V=${2//[[:space:]]/}
      case "$V" in ""|--*) die args "$1 needs a value, not '$2'";; esac;;
    *) die args "unknown argument $1 (see the usage at the top of this file)";;
  esac
  case "$1" in
    --plan-wt) PW=$2;; --loop-wt) LW=$2;; --plan-branch) PB=$2;; --loop-branch) LB=$2;; --plan) PLAN=$2;;
    --scope) SCOPE=$2;; --budget-usd) BUDGET=$2;; --deadline) DEADLINE=$2;; --reason) REASON=$2;; --drain-s) DRAIN_S=$2;;
    --probe-cap-usd) PROBE_CAP=$2;;
    *) ROLES+=("$1" "$2");;   # a launcher option: kept for start_run.sh as given, never read here
  esac
  shift 2
done
[ -n "$PW" ] && [ -n "$LW" ] && [ -n "$PB" ] && [ -n "$LB" ] || die args "--plan-wt, --loop-wt, --plan-branch and --loop-branch are required"
[ "$PB" != "$LB" ] || die args "the plan branch and the loop branch are the same branch ($PB)"
case "$DRAIN_S" in *[!0-9]*) die args "--drain-s is a whole number of seconds";; esac
if [ -n "$PROBE_CAP" ]; then   # LC3.b: a live probe runs only under a positive cap, checked before any check runs
  case "$PROBE_CAP" in *[!0-9.]*|.*|*.|*.*.*) die args "--probe-cap-usd must be a positive number";; esac
  awk -v c="$PROBE_CAP" 'BEGIN { exit !(c + 0 > 0) }' || die args "--probe-cap-usd must be greater than 0"
fi
if [ "$MODE" != planonly ]; then
  case "$DEADLINE" in
    [0-2][0-9]:[0-5][0-9])
      if [ "$(date +%H:%M)" \< "$DEADLINE" ]; then DEADLINE="$(date +%Y-%m-%d) $DEADLINE"; else DEADLINE="$(date -v+1d +%Y-%m-%d) $DEADLINE"; fi;;
    [0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]\ [0-2][0-9]:[0-5][0-9]) ;;
    *) die args "--deadline HH:MM or \"YYYY-MM-DD HH:MM\" is required";;
  esac
fi
if [ "$MODE" = run ]; then
  [ -n "$PLAN" ] && [ -n "$SCOPE" ] && [ -n "$BUDGET" ] || die args "a redeploy launches: --plan, --scope and --budget-usd are required"
fi
for d in "$PW" "$LW"; do [ -d "$d" ] || die args "no folder $d"; done
PW=$(cd "$PW" && pwd -P); LW=$(cd "$LW" && pwd -P)
[ "$PW" != "$LW" ] || die args "--plan-wt and --loop-wt are the same folder ($PW)"

SROOT=${BROTHER_SCRATCH:-$HOME/.claude/brother-scratch}
mkdir -p "$SROOT" 2>/dev/null || die scratch "cannot create $SROOT"
S=$(mktemp -d "$SROOT/redeploy-XXXXXX") || die scratch "cannot create a scratch folder under $SROOT"
trap 'rm -rf "$S"' EXIT
mkdir -p "$S/objects" "$S/source" || die scratch "cannot prepare $S"
EVD=${START_RUN_EVIDENCE:-$HOME/.claude/evidence}

NP=0; NF=0; NN=0
row() {   # row STATE NAME DETAIL: one verdict line, counted
  case "$1" in PASS) NP=$((NP + 1));; FAIL) NF=$((NF + 1));; *) NN=$((NN + 1));; esac
  printf '%-7s %-9s %s\n' "$1" "$2" "$3"
}
take() {  # take NAME TEXT: every "PASS|FAIL|NO-DATA detail" line of TEXT is a verdict, any other line is shown indented
  local n=$1 got=0 line
  while IFS= read -r line; do
    case "${line%% *}" in PASS|FAIL|NO-DATA) row "${line%% *}" "$n" "${line#* }"; got=1;; *) [ -z "$line" ] || echo "        $line";; esac
  done <<< "$2"
  [ "$got" = 1 ] || row NO-DATA "$n" "the check printed no verdict"
}
py() {    # py [ARGS] <<'PY' ... : python with this file's folder as argv[1], run in the loop worktree (model_router's repo)
  (cd "$LW" && python3 -B - "$HERE" "$@")
}

# THE TREE: the facts start_run.sh step 1 reads (a named branch, an upstream, clean, not behind it), read without a
# fetch: ls-remote names the upstream's sha, and a sha this checkout does not hold is a commit it lacks.
REMOTE_PB=""; REMOTE_LB=""
check_tree() {   # check_tree DIR BRANCH NAME
  local w=$1 b=$2 n=$3 cur up rem rsha dirty ahead
  git -C "$w" rev-parse --git-dir >/dev/null 2>&1 || { row FAIL "$n" "$w is not a git checkout"; return 1; }
  cur=$(git -C "$w" branch --show-current 2>/dev/null)
  [ "$cur" = "$b" ] || { row FAIL "$n" "$w is on '${cur:-a detached HEAD}', not $b: check out $b there"; return 1; }
  up=$(git -C "$w" rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null) || { row FAIL "$n" "$b has no upstream: git -C $w push -u <remote> $b:$b"; return 1; }
  rem=${up%%/*}
  dirty=$(git -C "$w" --no-optional-locks status --porcelain 2>/dev/null | wc -l | tr -d ' ') || { row NO-DATA "$n" "git status could not run in $w"; return 1; }
  [ "$dirty" = 0 ] || { row FAIL "$n" "$dirty changed path(s) in $w: commit and push them, or move them out (a landing in flight reads dirty: run --check again)"; return 1; }
  rsha=$(git -C "$w" ls-remote "$rem" "refs/heads/${up#*/}" 2>/dev/null | awk 'NR == 1 {print $1}') || { row NO-DATA "$n" "cannot read the remote $rem"; return 1; }
  [ -n "$rsha" ] || { row FAIL "$n" "$up is not on $rem: git -C $w push -u $rem $b:${up#*/}"; return 1; }
  if git -C "$w" merge-base --is-ancestor "$rsha" HEAD 2>/dev/null; then
    ahead=$(git -C "$w" rev-list --count "$rsha..HEAD")
    row PASS "$n" "$b in $w is clean and holds $up ($ahead commit(s) ahead, pushed by the redeploy)"
  else
    row FAIL "$n" "$up has commits $w lacks: git -C $w pull --ff-only"; return 1
  fi
  if [ "$n" = plan ]; then REMOTE_PB=$rem; else REMOTE_LB=$rem; fi
  return 0
}

OBJ=""; MERGED=""
gits() {  # git in the plan worktree with every new object written to the scratch store, none to the repository
  GIT_OBJECT_DIRECTORY="$S/objects" GIT_ALTERNATE_OBJECT_DIRECTORIES="$OBJ" git -C "$PW" "$@"
}
check_merge() {  # the tree the redeploy's merge would make, computed with no change to either checkout
  local out rc
  OBJ=$(git -C "$PW" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/objects
  [ -d "$OBJ" ] || { row NO-DATA merge "the object store of $PW cannot be found"; return 1; }
  git -C "$PW" rev-parse -q --verify "refs/heads/$LB" >/dev/null || { row FAIL merge "the loop branch $LB is not a branch of the plan worktree's repository"; return 1; }
  out=$(gits merge-tree --write-tree --name-only --no-messages "refs/heads/$PB" "refs/heads/$LB" 2>&1); rc=$?
  case "$rc" in
    0) MERGED=$(printf '%s\n' "$out" | head -1); row PASS merge "$LB merges into $PB cleanly (tree ${MERGED:0:12})";;
    1) row FAIL merge "$LB conflicts with $PB in: $(printf '%s\n' "$out" | tail -n +2 | awk 'NF' | tr '\n' ' ')(merge it by hand in $PW first)"; return 1;;
    *) row NO-DATA merge "git merge-tree could not run (exit $rc): $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"; return 1;;
  esac
}

CONFIGS=""
read_configs() {  # the docs/plan files the deploy ships, from deploy_stamped itself
  CONFIGS=$(py <<'PY' 2>/dev/null
import sys; sys.path.insert(0, sys.argv[1])
import deploy_stamped
print(" ".join(deploy_stamped.CONFIGS))
PY
)
  [ -n "$CONFIGS" ]
}

run_checks() {
  local out rc paths p i roles=""
  # the roles a launch would use: a line, never a verdict (the launcher checks its own options)
  for ((i = 0; i < ${#ROLES[@]}; i += 2)); do roles="$roles ${ROLES[i]} '${ROLES[i + 1]}'"; done
  if [ -n "$roles" ]; then echo "ROLES   a launch would pass to start_run.sh:$roles"; else echo "ROLES   none given: a launch uses start_run.sh's own defaults"; fi
  # power first (L12): the driver's own rule, with its remedy first on battery, exactly as start_run.sh reads it
  out=$(python3 -B "$HERE/power_rule.py" check --deadline "$DEADLINE" 2>&1); rc=$?
  case "$rc" in
    0) row PASS power "${out:-on AC}";;
    2) [ "$(python3 -B "$HERE/power_rule.py" source 2>/dev/null)" = battery ] && out="plug in the charger first: $out"
       row FAIL power "$out";;
    *) row NO-DATA power "the power rule could not run (exit $rc): $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)";;
  esac
  # controls: start_run.sh refuses a HOLD, and this redeploy's own drain and resume would lift a PAUSE nobody asked it to
  if [ -e "$EVD/LOOP-HOLD.txt" ] || [ -L "$EVD/LOOP-HOLD.txt" ]; then
    row FAIL controls "a HOLD is in force ($(head -1 "$EVD/LOOP-HOLD.txt" 2>/dev/null | cut -c1-100)): the launcher refuses it; lift it on the owner's word first"
  elif [ -e "$EVD/LOOP-PAUSE.txt" ] || [ -L "$EVD/LOOP-PAUSE.txt" ]; then
    row FAIL controls "a PAUSE is in force ($(head -1 "$EVD/LOOP-PAUSE.txt" 2>/dev/null | cut -c1-100)): the redeploy's resume would lift it; decide it first (stop_loop.sh --resume)"
  else
    row PASS controls "no hold or pause in $EVD"
  fi
  # live: deploy_stamped's own quiet() question; a running loop is what the redeploy drains, never a blocker here
  out=$(bash "$HERE/stop_loop.sh" --dry 2>&1); rc=$?
  case "$rc" in
    0) row PASS live "no loop is running";;
    1) row PASS live "a loop is running; the redeploy drains and stops it (this check stopped nothing)";;
    *) row NO-DATA live "the live check could not answer (exit $rc): $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)";;
  esac
  check_tree "$PW" "$PB" plan
  check_tree "$LW" "$LB" loop
  check_merge
  # pins and seat: the programs start_run.sh step 2 resolves, and the intake's own seat gate over the Claude one
  out=$(py <<'PY' 2>&1
import os, sys; sys.path.insert(0, sys.argv[1])
bad, got = [], []
for var, which in (("BROTHER_CLAUDE_BIN", "claude"), ("BROTHER_CODEX_BIN", "codex")):
    cur = os.environ.get(var) or ""
    if not cur:
        try:
            import model_router
            cur = getattr(model_router, which + "_bin")() or ""
        except Exception as exc:
            print("NO-DATA %s could not be resolved (%s: %s)" % (var, type(exc).__name__, str(exc)[:100])); sys.exit(0)
    (got if os.path.isfile(cur) and os.access(cur, os.X_OK) else bad).append((var, cur))
if bad:
    print("FAIL " + "; ".join("%s resolves to '%s', not an executable file: set %s=<the real program>" % (v, c, v) for v, c in bad))
else:
    print("PASS " + " ".join("%s=%s" % vc for vc in got))
PY
)
  take pins "$out"
  if [ -z "$PROBE_CAP" ]; then
    row NO-DATA seat "no cap: a live probe needs --probe-cap-usd"
  else
  out=$(BROTHER_SCRATCH="$S" PROBE_CAP="$PROBE_CAP" py <<'PY' 2>&1
import os, sys; sys.path.insert(0, sys.argv[1])
try:
    prog = os.environ.get("BROTHER_CLAUDE_BIN") or ""
    if not prog:
        import model_router
        prog = model_router.claude_bin()
    import loop_intake
    got = loop_intake.seat_gate([{"transport": "claude", "status": "OK", "program": prog, "cause": "asked by redeploy --check", "cap_usd": os.environ.get("PROBE_CAP")}])[0]
except Exception as exc:
    print("NO-DATA the seat gate could not run (%s: %s)" % (type(exc).__name__, str(exc)[:160])); sys.exit(0)
if got.get("status") == "OK" and got.get("cause") == "asked by redeploy --check":
    print("PASS %s: no native seat will run (BROTHER_CLAUDE_NATIVE off or no sandbox-exec), so the intake asks no seat" % prog)
elif got.get("status") == "OK":
    print("PASS %s: %s" % (prog, got.get("cause")))
else:
    print("FAIL %s: %s%s" % (prog, got.get("cause"), "; remedy: %s" % got["remedy"] if got.get("remedy") else ""))
PY
)
  take seat "$out"
  fi
  # LC3.a doctor: read only, over the plan worktree, one verdict per capability and never BLOCKED. A host that is
  # not the certified one reads NO-DATA with the words "not certified", and the doctor is not run at all.
  host=$(uname -s 2>/dev/null || echo unknown)
  if [ "$host" != "$HOST_SUPPORTED" ]; then
    row NO-DATA doctor "the host is not certified: $host"
  else
    doc_out=$(python3 -B "$DOCTOR" --json --repo "$PW" 2>&1); doc_rc=$?
    doc=$(DOC_OUT="$doc_out" DOC_RC="$doc_rc" EXPECTED="$EXPECTED_CONTROLS" python3 -B - 2>&1 <<'PY'
import json, os, sys
rc, raw = os.environ.get("DOC_RC", ""), os.environ.get("DOC_OUT", "")
expected = [n for n in os.environ.get("EXPECTED", "").split(",") if n]
if rc != "0":
    print("FAIL the doctor refused (exit %s): %s" % (rc, " ".join(raw.split())[:300])); sys.exit(0)
try:
    d = json.loads(raw)
except ValueError as exc:
    print("NO-DATA the doctor printed no JSON (%s): %s" % (type(exc).__name__, " ".join(raw.split())[:300])); sys.exit(0)
if not isinstance(d, dict):
    print("NO-DATA the doctor printed no JSON (not an object): %s" % " ".join(raw.split())[:300]); sys.exit(0)
controls = d.get("controls", [])
if not isinstance(controls, list):
    print("FAIL the doctor gave no list of controls"); sys.exit(0)
if len(controls) != 13:
    print("FAIL the doctor gave %d verdicts, expected 13" % len(controls)); sys.exit(0)
if any(not isinstance(c, dict) for c in controls):
    print("FAIL a control has no name"); sys.exit(0)
names = [c.get("control") for c in controls]
if any(not isinstance(n, str) or not n for n in names):
    print("FAIL a control has no name"); sys.exit(0)
if len(set(names)) != 13:
    print("FAIL duplicate control names: %s" % ", ".join(sorted(names))); sys.exit(0)
if set(names) != set(expected):
    print("FAIL control names not in the expected set: %s" % ", ".join(sorted(set(names) - set(expected)))); sys.exit(0)
bad = [c.get("control") for c in controls if c.get("status") not in ("PRESENT", "ABSENT", "NO-DATA")]
if bad:
    print("FAIL control %s reads a status outside PRESENT, ABSENT, NO-DATA" % ", ".join(bad)); sys.exit(0)
nodata = [c.get("control") for c in controls if c.get("status") == "NO-DATA"]
if nodata:
    print("NO-DATA %d control(s) read NO-DATA: %s" % (len(nodata), ", ".join(nodata))); sys.exit(0)
print("PASS 13 capabilities, no BLOCKED, no NO-DATA")
PY
)
    take doctor "$doc"
  fi
  # stage, parity, canary: on the merged tree, in the scratch, the way deploy_stamped.py stages and proves it
  if [ -z "$MERGED" ] || ! read_configs; then
    for p in stage parity canary; do row NO-DATA "$p" "no merged tree to read (see merge above) or deploy_stamped could not be read"; done
    return 0
  fi
  paths="scripts"
  gits cat-file -e "$MERGED:plugin/runtime" 2>/dev/null && paths="$paths plugin/runtime"
  for p in $CONFIGS; do gits cat-file -e "$MERGED:docs/plan/$p" 2>/dev/null && paths="$paths docs/plan/$p"; done
  # shellcheck disable=SC2086
  if ! gits archive --format=tar "$MERGED" -- $paths | tar -x -f - -C "$S/source"; then
    for p in stage parity canary; do row NO-DATA "$p" "the merged tree could not be unpacked into $S/source"; done
    return 0
  fi
  if [[ " $paths " == *" plugin/runtime "* ]]; then
    out=$(py "$S/source" "$S/bin/candidate" <<'PY' 2>&1
import sys; sys.path.insert(0, sys.argv[1])
try:
    import deploy_stamped
except Exception as exc:
    print("NO-DATA deploy_stamped could not be imported (%s: %s)" % (type(exc).__name__, str(exc)[:160])); sys.exit(0)
try:
    n = len(deploy_stamped.stage_candidate(sys.argv[2], sys.argv[3]))
except Exception as exc:
    print("FAIL the deploy refuses the candidate: %s: %s" % (type(exc).__name__, str(exc)[:300])); sys.exit(0)
print("PASS %d candidate files staged from the merged tree" % n)
PY
)
    take stage "$out"
  else
    row PASS stage "the merged tree ships no plugin/runtime; the deploy stages no candidate"
  fi
  out=$(py "$S/source" "$S/bin" <<'PY' 2>&1
import os, subprocess, sys; sys.path.insert(0, sys.argv[1])
src, scratch_bin = sys.argv[2], sys.argv[3]
try:
    import deploy_stamped, tool_stamp
    target = os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin")))
    if not os.path.isdir(target):
        print("FAIL %s is not a directory: the deploy refuses a target that does not exist" % target); sys.exit(0)
    os.makedirs(scratch_bin, exist_ok=True)
    for d in (target, os.path.join(src, "scripts", "loop")):   # deploy_stamped.deploy's own order: the bin, then the tree
        tool_stamp.copy_tools(d, scratch_bin, skip=(deploy_stamped.STAMP,))
    parity = os.path.join(src, "scripts", "test_loop_tool_parity.py")
    if not os.path.isfile(parity):
        print("NO-DATA the merged tree has no scripts/test_loop_tool_parity.py"); sys.exit(0)
    r = subprocess.run([sys.executable, "-B", parity], cwd=src, env=dict(os.environ, BROTHER_DEPLOY_TARGET=scratch_bin),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=1800)
except Exception as exc:
    print("NO-DATA the scratch deploy could not be built (%s: %s)" % (type(exc).__name__, str(exc)[:160])); sys.exit(0)
lines = [l for l in r.stdout.splitlines() if l.strip()]
if r.returncode == 0:
    print("PASS the deployed tools would match the merged tree: %s" % (lines[-1][:120] if lines else "exit 0"))
else:
    fails = [l.strip() for l in lines if l.startswith("FAIL")] or lines[-3:]
    print("FAIL the deploy's parity would refuse after its swap (exit %d): %s" % (r.returncode, " | ".join(fails)[:400]))
PY
)
  take parity "$out"
  out=$(py "$S/source" "$LW" <<'PY' 2>&1
import os, subprocess, sys; sys.path.insert(0, sys.argv[1])
src, repo = sys.argv[2], sys.argv[3]
try:
    import loop_canary as C
    target = os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin")))
    spec_file = next((p for p in (os.path.join(src, "docs", "plan", "loop-canary.json"),
                                  os.path.join(os.path.dirname(target), "loop-canary.json")) if os.path.exists(p)), None)
    spec = C.load_spec(spec_file) if spec_file else None
    if spec is None:
        print("FAIL CANARY NO-DATA ahead: no readable canary spec with sub, base_commit and build (%s); the intake refuses it" % spec_file); sys.exit(0)
    missing = C.missing_fixture_files(spec)
    fixtures = C.fixtures_of(spec)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    unknown = [fx["sub"] for fx in fixtures
               if subprocess.run(["git", "rev-parse", "--verify", "-q", fx["base_commit"] + "^{commit}"], cwd=repo, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0]
except Exception as exc:
    print("NO-DATA the canary fixtures could not be read (%s: %s)" % (type(exc).__name__, str(exc)[:160])); sys.exit(0)
why = (["build file missing: " + ", ".join(missing)] if missing else []) + (["base commit not in %s: %s" % (repo, ", ".join(unknown))] if unknown else [])
if why:
    print("FAIL CANARY NO-DATA ahead: %s" % "; ".join(why))
else:
    print("PASS %d fixture(s): every build file is here and every base commit resolves" % len(fixtures))
PY
)
  take canary "$out"
}

verdict() {   # the summary line and the check mode's exit code: FAIL outranks NO-DATA, NO-DATA outranks PASS
  local v=PASS code=0
  [ "$NN" = 0 ] || { v=NO-DATA; code=2; }
  [ "$NF" = 0 ] || { v=FAIL; code=1; }
  echo "CHECK $v: $NP PASS, $NF FAIL, $NN NO-DATA; nothing was stopped, drained or pushed"
  return $code
}

if [ "$MODE" = check ]; then
  run_checks
  verdict
  exit $?
fi

stop_loop() { bash "$HERE/stop_loop.sh" "$@"; }
merge_and_push() {   # the loop line into the plan, both lines pushed; the loop worktree fast forwarded to the plan
  git -C "$PW" merge --no-ff -q "$LB" -m "merge $LB before $1" >&2 || { git -C "$PW" merge --abort 2>/dev/null; echo "the merge of $LB into $PB failed and was aborted"; return 1; }
  if [ "$1" = redeploy ]; then
    (cd "$PW" && python3 -B scripts/test_bl_owns_complete.py >&2) || { echo "OWNS RED after the merge: the merge commit stays in $PW, unpushed"; return 1; }
  fi
  git -C "$PW" push -q "$REMOTE_PB" "$PB" >&2 || { echo "the plan push failed: the merge commit stays in $PW, unpushed"; return 1; }
  git -C "$LW" merge -q --ff-only "$PB" >&2 || { echo "$LW could not fast forward to $PB"; return 1; }
  git -C "$LW" push -q "$REMOTE_LB" "$LB" >&2 || { echo "the loop line push failed: $LW holds $PB, unpushed"; return 1; }
}

if [ "$MODE" = planonly ]; then
  { check_tree "$PW" "$PB" plan && check_tree "$LW" "$LB" loop && check_merge; } || die tree "the checkouts are not ready (above); nothing was paused"
  read_configs || die configs "deploy_stamped.CONFIGS could not be read; nothing was paused"
  CHANGED=$(gits diff --name-only "refs/heads/$LB" "$MERGED") || die diff "the change could not be listed; nothing was paused"
  [ -n "$CHANGED" ] || { echo "PLAN-ONLY nothing to merge: $LB already holds $PB"; exit 0; }
  BAD=0
  while IFS= read -r f; do
    case "$f" in
      docs/*) for c in $CONFIGS; do [ "$f" = "docs/plan/$c" ] && { echo "REFUSED  $f: the deploy ships it, so it needs the full redeploy"; BAD=1; }; done;;
      *) echo "REFUSED  $f: not a plan, spec or notes document (only docs/ is plan only)"; BAD=1;;
    esac
  done <<< "$CHANGED"
  [ "$BAD" = 0 ] || die plan-only "the change touches the path(s) above; run the full redeploy instead. Nothing was paused"
  echo "PLAN-ONLY $(printf '%s\n' "$CHANGED" | wc -l | tr -d ' ') document path(s): $(printf '%s' "$CHANGED" | tr '\n' ' ')"
  stop_loop --drain "$DRAIN_S" --reason "$REASON (plan only)"; rc=$?
  [ "$rc" = 0 ] || { stop_loop --resume; die drain "the drain did not finish (exit $rc); the loop is resumed as it was, nothing was merged"; }
  WHY=$(merge_and_push "plan only merge") || { stop_loop --resume; die merge "$WHY; the loop is resumed"; }
  stop_loop --resume
  echo "PLAN-ONLY DONE: $LB at $(git -C "$LW" rev-parse --short HEAD); the loop was paused at a pass boundary, never stopped, and is resumed"
  exit 0
fi

# default: the whole redeploy, the check first
run_checks
verdict || die check "the check above found $NF FAIL and $NN NO-DATA; nothing was drained, stopped or pushed"
stop_loop --drain "$DRAIN_S" --reason "$REASON"; rc=$?
[ "$rc" = 0 ] || { stop_loop --resume; die drain "the drain did not finish (exit $rc); the loop is resumed, nothing was stopped"; }
stop_loop --reason "$REASON" || die stop "the stop left something alive (above); the loop is PAUSED and partly stopped: finish it with stop_loop.sh --reason, then run again"
stop_loop --resume
WHY=$(merge_and_push redeploy) || die merge "$WHY; the loop is STOPPED"
echo "OK   lines merged and pushed: $LB at $(git -C "$LW" rev-parse --short HEAD)"
(cd "$LW" && bash "${REDEPLOY_DEPLOY:-$LW/scripts/loop/deploy_stamped.sh}") || die deploy "the deploy refused (above; its RESTORE line is the way back); the loop is STOPPED"
STAMPED=$(py <<'PY' 2>&1
import json, os, sys; sys.path.insert(0, sys.argv[1])
import deploy_stamped
target = os.path.abspath(os.path.expanduser(os.environ.get("BROTHER_DEPLOY_TARGET", "~/.claude/bin")))
with open(os.path.join(target, deploy_stamped.STAMP), encoding="utf-8") as fh:
    print(json.load(fh)["revision"])
PY
) || die stamp "the deployed stamp could not be read: $(printf '%s' "$STAMPED" | tail -1); the loop is STOPPED"
HEAD=$(git -C "$LW" rev-parse HEAD)
[ "$STAMPED" = "$HEAD" ] || die stamp "the deployed stamp names $STAMPED, not the loop line's $HEAD; the loop is STOPPED"
echo "OK   stamp  the deployed tools are $STAMPED, the loop line's HEAD"
(cd "$LW" && bash "${REDEPLOY_START:-$LW/scripts/loop/start_run.sh}" --plan "$PLAN" --deadline "$DEADLINE" --budget-usd "$BUDGET" --scope "$SCOPE" ${ROLES[@]+"${ROLES[@]}"}) \
  || die start "the launcher refused (above); the new tools are deployed and the loop is STOPPED"
echo "REDEPLOY DONE: $STAMPED deployed and launched"
