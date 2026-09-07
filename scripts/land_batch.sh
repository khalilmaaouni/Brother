#!/bin/bash
# land_batch.sh: batch lander. Gates ONE integration tree built from many pull
# requests, regenerates the same generated files regate.sh regenerates on a
# single PR (SYSTEM.md, the runtime bundle, the product manifests) and commits
# them on that tree, then lands the whole thing as ONE integration pull
# request: push the tree, open one PR listing every included PR, merge it,
# and read each included PR's own state back from GitHub (a merge commit
# carries every included PR's head commit into main, which is what marks
# each of them merged). Keeps the two laws land_one.sh/regate.sh/
# merge_if_green.sh already hold: gate and merge are separate commands, and
# there is one writer to main (this script is that one writer for its own
# run; the caller stops the serial runner first so two writers never race).
#
# Usage:
#   scripts/land_batch.sh [--dry-run] <pr> [<pr> ...]
#   scripts/land_batch.sh [--dry-run] --queue
#
# --queue reads ~/.claude/evidence/land-queue.txt (one PR number per line,
# extra tokens on a line ignored: this lander does not run per-PR extra
# checks the way land_one.sh does). Any PR left unmerged (a conflict, a
# refused GitHub merge, a red gate half) is written back to that file for the
# serial lander to pick up later.
#
# Env overrides, for the self test only; production runs on the defaults:
#   LAND_BATCH_REPO        checkout to work from        (default: ~/brother-hub)
#   LAND_BATCH_REPO_SLUG   gh repo slug                  (default: khalilmaaouni/brother-hub)
#   LAND_BATCH_EVIDENCE    evidence directory            (default: ~/.claude/evidence)
#   LAND_BATCH_SERIAL_PID  pid of the running serial runner, so a dead pid can
#                          override a stale mid-PR line in serial-queue.log
#
# Exit codes: 0 all requested PRs merged (or a clean dry run); 1 red gate, a
# partial merge, or a dry run that read a red gate; 2 NO-DATA (serial runner
# mid PR, empty queue, no PR numbers, network/worktree failure).
set -u

REPO_SLUG="${LAND_BATCH_REPO_SLUG:-khalilmaaouni/brother-hub}"
REPO_DIR="${LAND_BATCH_REPO:-$HOME/brother-hub}"
EVDIR="${LAND_BATCH_EVIDENCE:-$HOME/.claude/evidence}"
QUEUE="$EVDIR/land-queue.txt"
SERIAL_QLOG="$EVDIR/serial-queue.log"
LAND_LOG="$EVDIR/serial-land.log"
LOCK="$EVDIR/land-batch.lock"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
WT="/private/tmp/bh-batch-$STAMP"
GATE_LOG="$EVDIR/batch-gate-$STAMP.log"

dry_run=0
use_queue=0
prs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry_run=1; shift;;
    --queue) use_queue=1; shift;;
    *) prs+=("$1"); shift;;
  esac
done

mkdir -p "$EVDIR"

if [ "$use_queue" = 1 ]; then
  if [ ! -s "$QUEUE" ]; then echo "land_batch: NO-DATA empty queue $QUEUE"; exit 2; fi
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    prs+=("$(echo "$line" | awk '{print $1}')")
  done < "$QUEUE"
fi

if [ "${#prs[@]}" -eq 0 ]; then echo "land_batch: NO-DATA no PR numbers given"; exit 2; fi

# --- step 2: refuse to start while the serial runner is mid PR (real run only) ---
if [ "$dry_run" = 0 ]; then
  if [ -f "$SERIAL_QLOG" ]; then
    last="$(tail -n 1 "$SERIAL_QLOG")"
    case "$last" in
      *" START "*)
        pid="${LAND_BATCH_SERIAL_PID:-}"
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
          echo "land_batch: serial runner pid $pid is dead despite '$last', taking the queue over"
        else
          echo "land_batch: NO-DATA serial runner is mid PR ($last), refusing to start"
          exit 2
        fi
        ;;
      *) ;; # last line is an END (or something else harmless): fine to proceed
    esac
  fi
  echo "batch $STAMP started $(date -u +%FT%TZ) pid $$ prs: ${prs[*]}" > "$LOCK"
fi

LEFTOVER=()
note_leftover() {  # note_leftover <pr> <reason>
  LEFTOVER+=("$1")
  if [ "$dry_run" = 1 ]; then echo "land_batch: would leave $1 queued: $2"
  else echo "land_batch: leaving $1 queued: $2"; fi
}

# Worktree and lock cleanup is safe on ANY exit (idempotent, destroys
# nothing that was not this run's own scratch state). Rewriting the queue
# file is NOT: it must only happen once every PR has been classified (merged
# candidate, dropped, or leftover), never on an early NO-DATA abort (network,
# worktree creation) where nothing has been decided yet and the original
# queue file must be left exactly as found. flush_queue() below is called
# explicitly at each post-classification exit; it is never part of this trap.
CLEANUP_WT=()
cleanup() {
  local w
  for w in "${CLEANUP_WT[@]:-}"; do
    [ -n "$w" ] && [ -d "$w" ] && git -C "$REPO_DIR" worktree remove --force "$w" >/dev/null 2>&1
  done
  [ "$dry_run" = 0 ] && rm -f "$LOCK"
}
trap cleanup EXIT

flush_queue() {  # rewrite the queue file with exactly the leftover PRs (real run only)
  [ "$dry_run" = 1 ] && return 0
  if [ "$use_queue" = 1 ]; then : > "$QUEUE"; fi
  local n
  for n in "${LEFTOVER[@]:-}"; do [ -n "$n" ] && echo "$n" >> "$QUEUE"; done
}

# --- step 3: build the integration worktree from origin/main ---
cd "$REPO_DIR" || { echo "land_batch: NO-DATA cannot cd to $REPO_DIR"; exit 2; }
git fetch -q origin || { echo "land_batch: NO-DATA network, git fetch origin failed"; exit 2; }
git worktree add -q "$WT" origin/main || { echo "land_batch: NO-DATA cannot create integration worktree $WT"; exit 2; }
CLEANUP_WT+=("$WT")
(cd "$WT" && git checkout -q -b "batch-$STAMP") || { echo "land_batch: NO-DATA cannot branch the integration worktree"; exit 2; }

check_pr_state() {  # sets PR_STATE, PR_BASE; 0 ok, 1 gh view failed, 2 not open, 3 wrong base
  local n="$1" out
  out="$(gh pr view "$n" --repo "$REPO_SLUG" --json state,baseRefName --jq '[.state,.baseRefName]|@tsv' 2>&1)" || { PR_ERR="$out"; return 1; }
  IFS=$'\t' read -r PR_STATE PR_BASE <<< "$out"
  [ -z "${PR_STATE:-}" ] && return 1
  [ "$PR_STATE" != "OPEN" ] && return 2
  [ "$PR_BASE" != "main" ] && return 3
  return 0
}

try_merge_pr() {  # try_merge_pr <worktree> <n>: 0 merged clean, 1 conflict (aborted)
  local wt="$1" n="$2"
  git -C "$wt" fetch -q origin "pull/$n/head" 2>>"$GATE_LOG" || return 1
  if git -C "$wt" merge --no-ff --no-edit -q FETCH_HEAD 2>>"$GATE_LOG"; then return 0; fi
  git -C "$wt" merge --abort >/dev/null 2>&1
  return 1
}

MERGED=()
for n in "${prs[@]}"; do
  check_pr_state "$n"; rc=$?
  case $rc in
    1) note_leftover "$n" "gh pr view failed: ${PR_ERR:-no data}"; continue;;
    2) case "$PR_STATE" in
         MERGED) echo "land_batch: $n already MERGED upstream, dropping from queue";;
         *) echo "land_batch: $n is $PR_STATE, dropping from queue";;
       esac
       continue;;
    3) note_leftover "$n" "base is $PR_BASE, not main"; continue;;
  esac
  if try_merge_pr "$WT" "$n"; then
    MERGED+=("$n")
  else
    note_leftover "$n" "conflicts merging pull/$n/head into the integration branch"
  fi
done

if [ "${#MERGED[@]}" -eq 0 ]; then
  echo "land_batch: NO-DATA nothing merged cleanly into the integration tree"
  flush_queue
  exit 2
fi

# --- regenerate the same generated files regate.sh regenerates on a single PR,
# in the same order, and commit them on the integration tree. Errors here are
# swallowed exactly as regate.sh swallows them (a repo that lacks one of these
# generators, as the self test's fixtures mostly do, just leaves that step a
# no-op); only an actual diff gets committed. ---
regenerate_and_commit() {  # regenerate_and_commit <worktree> <label> <pr...>
  local wt="$1" label="$2"; shift 2
  ( cd "$wt" && python3 scripts/bundle_runtime.py >/dev/null 2>&1
    python3 scripts/system_doc.py >/dev/null 2>&1 )
  local p
  for p in products/brothermode products/brothersbe; do
    ( cd "$wt/$p" 2>/dev/null && sh scripts/checksums.sh CHECKSUMS.sha256 >/dev/null 2>&1 )
  done
  local f
  for f in bundle/runtime SYSTEM.md products/brothermode/CHECKSUMS.sha256 products/brothersbe/CHECKSUMS.sha256; do
    git -C "$wt" add -A -- "$f" >/dev/null 2>&1
  done
  if ! git -C "$wt" diff --cached --quiet; then
    git -C "$wt" commit -q -m "Regenerate SYSTEM.md, the runtime bundle and the product manifests for batch $label (PRs: $*)"
  fi
}

regenerate_and_commit "$WT" "$STAMP" "${MERGED[@]}"

# --- load: only a dry run's merge-step preview may skip the gate under high load ---
fifteen_min_load() {
  sysctl -n vm.loadavg 2>/dev/null | awk '{gsub(/[{}]/,""); print $3}'
}
high_load=0
load15="$(fifteen_min_load)"
if [ -n "$load15" ] && awk -v l="$load15" 'BEGIN{exit !(l>150)}'; then high_load=1; fi

run_gate() {  # run_gate <worktree> <logfile> <label>: sets RED_NAMES, 0 green / 1 red
  local wt="$1" log="$2" label="$3"
  {
    echo "=== BATCH GATE $label prs: ${MERGED[*]}"
    (cd "$wt" && LAND_BATCH_PRS="${MERGED[*]}" python3 scripts/bundle_runtime.py --check); echo "## bundle exit $?"
    (cd "$wt" && LAND_BATCH_PRS="${MERGED[*]}" sh scripts/required_fast.sh); echo "## required_fast exit $?"
    (cd "$wt" && LAND_BATCH_PRS="${MERGED[*]}" python3 scripts/gen_readiness_board.py --check) 2>&1 | tail -1
    echo "## board-check exit ${PIPESTATUS[0]}"
    echo "=== END BATCH GATE $label"
  } > "$log" 2>&1
  RED_NAMES=""
  grep -q '## bundle exit 0' "$log" || RED_NAMES="$RED_NAMES bundle_runtime"
  grep -q '## required_fast exit 0' "$log" || RED_NAMES="$RED_NAMES required_fast"
  grep -q '## board-check exit 0' "$log" || RED_NAMES="$RED_NAMES board-check"
  [ -z "$RED_NAMES" ]
}

if [ "$dry_run" = 1 ] && [ "$high_load" = 1 ]; then
  echo "land_batch: 15-min load average $load15 is above 150, skipping the gate (dry run merge-step preview only)"
  echo "land_batch: would merge (in order): ${MERGED[*]}"
  exit 0
fi

run_gate "$WT" "$GATE_LOG" "$STAMP"
gate_rc=$?

open_and_merge_integration_pr() {  # open_and_merge_integration_pr <worktree> <label> <gate-log> <pr...>: 0 every included PR reads MERGED, 1 stopped or a PR did not
  local wt="$1" label="$2" gate_log="$3"; shift 3
  local branch="batch/$label"
  if ! git -C "$wt" push -q origin "HEAD:refs/heads/$branch" 2>>"$gate_log"; then
    echo "land_batch: NO-DATA could not push the integration branch $branch"
    for n in "$@"; do note_leftover "$n" "could not push the integration branch $branch"; done
    return 1
  fi
  local n_included=$# numbers title n body t
  numbers="$*"; numbers="${numbers// /, }"
  title="Batch landing of $n_included pull requests: $numbers"
  body="Batch $label. Gate log: $gate_log"$'\n\nIncluded pull requests:'
  for n in "$@"; do
    t="$(gh pr view "$n" --repo "$REPO_SLUG" --json title --jq .title 2>/dev/null)"
    body="$body"$'\n'"- #$n: ${t:-unknown title}"
  done
  local pr_url pr_num
  pr_url="$(gh pr create --repo "$REPO_SLUG" --base main --head "$branch" --title "$title" --body "$body" 2>>"$gate_log" | tail -1)"
  if [ -z "$pr_url" ]; then
    echo "land_batch: NO-DATA gh pr create failed for batch $label"
    for n in "$@"; do note_leftover "$n" "could not open the integration pull request for batch $label"; done
    return 1
  fi
  pr_num="${pr_url##*/}"
  echo "land_batch: opened integration pull request $pr_num ($pr_url) for batch $label, PRs: $*"
  local merge_out merge_state
  merge_out="$(gh pr merge "$pr_num" --repo "$REPO_SLUG" --merge --delete-branch 2>&1 | tail -1)"
  merge_state="$(gh pr view "$pr_num" --repo "$REPO_SLUG" --json state --jq .state 2>/dev/null)"
  if [ "$merge_state" != "MERGED" ]; then
    echo "land_batch: STOP, GitHub REFUSED the integration pull request $pr_num for batch $label: $merge_out"
    for n in "$@"; do note_leftover "$n" "the integration pull request $pr_num for batch $label was refused by GitHub"; done
    return 1
  fi
  echo "land_batch: integration pull request $pr_num merged for batch $label"
  git -C "$REPO_DIR" fetch -q origin main >/dev/null 2>&1
  local rc=0 state
  for n in "$@"; do
    state="$(gh pr view "$n" --repo "$REPO_SLUG" --json state --jq .state 2>/dev/null)"
    if [ "$state" = "MERGED" ]; then
      echo "merge $n: state $state (batch $label via integration pull request $pr_num, gate $gate_log)" >> "$LAND_LOG"
      echo "LAND-$n-END" >> "$LAND_LOG"
      echo "land_batch: $n reads MERGED"
    else
      echo "land_batch: $n still reads ${state:-unknown} after $pr_num merged, reporting as still open"
      note_leftover "$n" "still reads ${state:-unknown} after the integration pull request $pr_num merged"
      rc=1
    fi
  done
  return $rc
}

if [ "$dry_run" = 1 ]; then
  echo "land_batch: would merge (in order): ${MERGED[*]}"
  if [ $gate_rc -eq 0 ]; then echo "land_batch: gate GREEN ($GATE_LOG)"; exit 0
  else echo "land_batch: gate RED:$RED_NAMES ($GATE_LOG)"; exit 1; fi
fi

if [ $gate_rc -eq 0 ]; then
  open_and_merge_integration_pr "$WT" "$STAMP" "$GATE_LOG" "${MERGED[@]}"
  final_rc=$?
  flush_queue
  exit $final_rc
fi

# --- red: print, merge nothing from the full set, bisect once ---
echo "BATCH RED:$RED_NAMES"
n_merged=${#MERGED[@]}
mid=$(( (n_merged + 1) / 2 ))
half1=("${MERGED[@]:0:mid}")
half2=("${MERGED[@]:mid}")
overall_rc=1

gate_and_merge_half() {  # gate_and_merge_half <label> <pr...>
  local label="$1"; shift
  [ $# -eq 0 ] && return 0
  local hw="/private/tmp/bh-batch-$STAMP-$label"
  git -C "$REPO_DIR" worktree add -q "$hw" origin/main || { for n in "$@"; do note_leftover "$n" "could not build the $label bisect worktree"; done; return 1; }
  CLEANUP_WT+=("$hw")
  (cd "$hw" && git checkout -q -b "batch-$STAMP-$label")
  local ok=()
  for n in "$@"; do
    if try_merge_pr "$hw" "$n"; then ok+=("$n")
    else note_leftover "$n" "conflicts merging into the $label bisect half"; fi
  done
  if [ "${#ok[@]}" -eq 0 ]; then return 0; fi
  regenerate_and_commit "$hw" "$STAMP-$label" "${ok[@]}"
  local hlog="$EVDIR/batch-gate-$STAMP-$label.log"
  local saved_merged=("${MERGED[@]}"); MERGED=("${ok[@]}")
  run_gate "$hw" "$hlog" "$STAMP-$label"
  local hrc=$?
  MERGED=("${saved_merged[@]}")
  if [ $hrc -eq 0 ]; then
    open_and_merge_integration_pr "$hw" "$STAMP-$label" "$hlog" "${ok[@]}"
  else
    echo "BATCH RED half $label:$RED_NAMES"
    for n in "${ok[@]}"; do note_leftover "$n" "the $label bisect half gated red:$RED_NAMES"; done
  fi
}

gate_and_merge_half "a" "${half1[@]:-}"
gate_and_merge_half "b" "${half2[@]:-}"
flush_queue
exit $overall_rc
