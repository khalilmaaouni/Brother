#!/bin/sh
# merge_verified.sh [--dry-run] [--wait SECONDS] <list>: gate and merge a pinned pull request
# list into main, in list order. The list is the LAST argument: anything after it is refused (exit 2)
# before anything runs, so `<list> --dry-run` is never a real run (review 2026-10-06, F7).
# With --wait, a tree with no row yet is waited for (the
# background pre-computation of MG1.c may still be gating it) up to SECONDS, through
# merge_precompute.wait_ready, which is the same verify_tree verdict or NO-DATA at the timeout.
#
# Every default path comes from this script's own directory or from the
# clone's git common dir. Nothing here points into a home folder.
#
# ONE WRITER, MERGE ONLY. EVERY merge is
# `gh pr merge --merge --match-head-commit "$sha"`, run by the owner from his
# terminal. This tool never pushes main: a head that moves under a pin (MG1.d,
# merge_pins.py: default pattern loop/run-*) is frozen on a merge-pin/ branch
# at the pinned sha, a pull request is opened from that branch, and THAT pull
# request reaches the same merge call. The loop's later commits are not merged.
# Every write (the frozen branch, its pull request, each merge) runs only after
# the ONE typed confirmation on this terminal (merge_pins.owner_confirmed).
#
# The tree gate has ONE definition, scripts/merge_gate.py (H4): the ledger
# lookup below is a call to it, never a second copy of the rule.
#
# Each list line: "<number> <sha> [...] [pin=<branch>] [frozen=<number>]"; the
# pin fields are written here, in place, after the freeze, so a rerun finds them.
#
# THE CLEAN START (review 2026-10-06: F1 DEVELOPER_DIR answered for /usr/bin/python3 and /usr/bin/git, which are one
# shim; F2 CDPATH moved HERE; F3 started with bash, a caller's functions and traps outlived the discipline below).
# Nothing this tool inherits is trusted: unless its environment already IS the allow list, it starts itself again
# under `/usr/bin/env -i` and /bin/sh, both by absolute path, with its arguments unchanged. What crosses, and why:
#   HOME           as given; a real run refuses one that is not the account's own (gh reads its identity there)
#   TERM           as given, kept only when it is a plain terminal name
#   PATH           never the caller's: the fixed system path this file spells
#   USER, LOGNAME  never the caller's: set here from the account (/usr/bin/id), because the keychain gh reads its
#                  login from is found by account name
#   MERGE_CLONE, MERGE_REMOTE, MERGE_BASE_BRANCH, MERGE_REPO, MERGE_PINS_MOVING
#                  this tool's own names. A dry run honours them as before; a real run refuses the first four by name
#                  (merge_gate.FORBIDDEN_NAMES); the fifth can only ADD heads to freeze (merge_pins.moving_patterns)
#   MERGE_VERIFIED_CALLER_PATH   DATA, never PATH: where the caller's gh is looked up, then judged by the rule below
#                  (a system directory, or the fixture), exactly as the caller's PATH was before
#   MERGE_VERIFIED_CALLER_NAMES  DATA: the NAMES the caller's environment carried, never a value, so a real run still
#                  refuses a steering name by name (merge_gate.py refuse-env) although nothing of it arrives
#   MERGE_VERIFIED_CLEAN         how many clean starts ran (1, then 2). It is no password: a caller that sets it gains
#                  nothing, because the test is the environment itself, line by line, and one name outside this list
#                  means another clean start. Still not clean at 2 is refused, so a clean start can never loop.
# THE BOUND (D0, stated, not solved): this removes every inherited NAME. It cannot remove code a caller's shell ran
# inside this very process before line one: bash reads BASH_ENV first, and any bash expands a PS4 that carries a
# command substitution when SHELLOPTS turns tracing on. Whoever can do that runs commands as the owner already.
#
# A bare assignment, before any command, so no function can stand in for it: posix mode makes the special builtins
# (set, unset, trap, exec, exit) outrank every function even when this file is started with bash, where an exported
# unset() swallowed the whole discipline below. Tracing goes off before the environment is read into a variable (an
# inherited xtrace would print every inherited value). Then the one trap that fires between lines is cleared: a DEBUG
# trap left by BASH_ENV rewrote the gate's exit code, and would rewrite the clean start's own variables as readily.
POSIXLY_CORRECT=1
set -u +x
trap - DEBUG 2>/dev/null
# EXPORTED FUNCTIONS IMPORTED BY /bin/sh (bash 3.2) OUTRANK BUILTINS: drop any for the names this tool relies on before
# anything runs (review round 21, 2026-10-04). unset is a special builtin, which no function can replace.
# '[' is no valid identifier in posix mode (measured, review round 22), so posix is left for that one unset; unset, set and
# builtin go first while posix mode still makes the special builtins outrank any function.
unset -f unset set builtin 2>/dev/null; set +o posix 2>/dev/null
# every external utility this file runs is unset too (review 2026-10-05: an exported head function chose the
# tree that was gated; the earlier rounds named only the builtins they had seen abused)
unset -f '[' true false cd pwd command printf test echo read export shift exit eval exec trap umask type hash wait grep awk bash cat chmod cut date dirname env find head id mkdir mktemp rm sh shasum tail 2>/dev/null
set -o posix 2>/dev/null || true
# Is the environment the allow list? Read once from /usr/bin/env, which is what every tool started here would inherit.
# A reading that is empty or cannot be had is never clean.
MV_PATH=/usr/bin:/bin:/usr/sbin:/sbin
MV_NL='
'
mv_env="$(/usr/bin/env 2>/dev/null)" || mv_env=""
mv_clean=no
case "${MERGE_VERIFIED_CLEAN-}" in
  1|2)
    case "$mv_env" in '') : ;; *) mv_clean=yes ;; esac
    mv_ifs="$IFS"; IFS="$MV_NL"; set -f
    for mv_line in $mv_env; do
      case "$mv_line" in
        "PATH=$MV_PATH"|MERGE_VERIFIED_CLEAN=1|MERGE_VERIFIED_CLEAN=2) : ;;
        HOME=*|TERM=*|PWD=*|OLDPWD=*|SHLVL=*|_=*) : ;;   # the last four are the shell's own
        MERGE_CLONE=*|MERGE_REMOTE=*|MERGE_BASE_BRANCH=*|MERGE_REPO=*|MERGE_PINS_MOVING=*) : ;;
        MERGE_VERIFIED_CALLER_PATH=*|MERGE_VERIFIED_CALLER_NAMES=*) : ;;
        *) mv_clean=no ;;
      esac
    done
    set +f; IFS="$mv_ifs" ;;
esac
case "$mv_clean" in
  yes) : ;;
  *)
    case "${MERGE_VERIFIED_CLEAN-}" in
      2) echo "STOP: the environment is still not clean after two clean starts (a value with a line break cannot cross one); nothing ran"
         exit 2 ;;
      1) mv_hop=2 ;;
      *) mv_hop=1 ;;
    esac
    # the caller's names, as data: only words shaped like a name, so a line of some value is never carried
    mv_names=""; mv_ifs="$IFS"; IFS="$MV_NL"; set -f
    for mv_line in $mv_env; do
      mv_name="${mv_line%%=*}"
      case "$mv_name" in ''|[0-9]*|*[!A-Za-z0-9_]*) : ;; *) mv_names="$mv_names $mv_name" ;; esac
    done
    set +f; IFS="$mv_ifs"
    exec /usr/bin/env -i ${HOME+"HOME=${HOME}"} ${TERM+"TERM=${TERM}"} "PATH=$MV_PATH" \
      "MERGE_VERIFIED_CLEAN=$mv_hop" "MERGE_VERIFIED_CALLER_PATH=${PATH-}" "MERGE_VERIFIED_CALLER_NAMES=$mv_names" \
      ${MERGE_CLONE+"MERGE_CLONE=${MERGE_CLONE}"} ${MERGE_REMOTE+"MERGE_REMOTE=${MERGE_REMOTE}"} \
      ${MERGE_BASE_BRANCH+"MERGE_BASE_BRANCH=${MERGE_BASE_BRANCH}"} ${MERGE_REPO+"MERGE_REPO=${MERGE_REPO}"} \
      ${MERGE_PINS_MOVING+"MERGE_PINS_MOVING=${MERGE_PINS_MOVING}"} \
      /bin/sh "$0" ${1+"$@"}
    echo "STOP: the clean start could not run /usr/bin/env; nothing ran"
    exit 2 ;;
esac
# CLEAN from here on, and nothing below reads a VALUE the test above let through without setting or judging it: the
# test only proves no foreign NAME is present (a value with line breaks can imitate lines, never hide a name). So the
# path is set here whatever arrived, the data leaves the environment before any tool starts, the terminal name is
# kept only when plain, and the account names are the account's own whatever the caller said.
PATH="$MV_PATH"; export PATH
mv_caller_path="${MERGE_VERIFIED_CALLER_PATH-}"; mv_caller_names="${MERGE_VERIFIED_CALLER_NAMES-}"
unset MERGE_VERIFIED_CLEAN MERGE_VERIFIED_CALLER_PATH MERGE_VERIFIED_CALLER_NAMES
case "${TERM-x}" in ''|*[!A-Za-z0-9._+-]*) unset TERM ;; esac
mv_account="$(/usr/bin/id -unr 2>/dev/null || true)"
[ -n "$mv_account" ] || { echo "NO-DATA: /usr/bin/id cannot name this account, so there is no USER to hand to gh; nothing ran"; exit 2; }
USER="$mv_account"; LOGNAME="$mv_account"; export USER LOGNAME
# PINNED TOOLS (review round 19, 2026-10-04): python3 and git by absolute system path, so a PATH entry ahead of them
# cannot answer for the gate. Functions, so every later call (and every subshell) goes through them.
PY=/usr/bin/python3; GIT=/usr/bin/git
{ [ -x "$PY" ] && [ -x "$GIT" ]; } || { echo "NO-DATA: /usr/bin/python3 or /usr/bin/git is missing"; exit 2; }
python3() { "$PY" -E -s "$@"; }   # -E -s: no PYTHONPATH, PYTHONSTARTUP or user site can load code into the gate (review 2026-10-05)
git() { "$GIT" "$@"; }
# HERE from shell builtins only (review round 20: a dirname earlier on PATH could point HERE, and with it the gate, the
# clone and the fixture marker, at a directory it controls). F2, review 2026-10-06: `cd scripts` went through CDPATH
# and printed its target, so HERE held two lines of a planted twin. CDPATH is gone after the clean start; the cd is
# still made unable to use it (an operand that starts with / or ./ is never looked up there, and its output is
# dropped), and a HERE that is not one absolute line is refused.
case "$0" in /*) _here_dir="${0%/*}" ;; */*) _here_dir="./${0%/*}" ;; *) _here_dir=. ;; esac
HERE="$(cd -P -- "$_here_dir" >/dev/null 2>&1 && pwd -P)" || HERE=""
case "$HERE" in
  ''|[!/]*|*"$MV_NL"*) echo "NO-DATA: this tool cannot name its own directory from '$0'; nothing ran"; exit 2 ;;
esac
# gh is the one tool looked up where the CALLER's PATH says, read as data and never made this shell's PATH: resolved
# here, checked below. Every other lookup (sh, shasum, ssh, mktemp, date, cut, head, tail) is on the system path.
GH_FOUND="$(PATH="$mv_caller_path" command -v gh 2>/dev/null || true)"
PY_GATE="$HERE/merge_gate.py"
PY_PRE="$HERE/merge_precompute.py"
PY_PINS="$HERE/merge_pins.py"
PY_REACH="$HERE/merge_reach.py"

DRY=0
WAIT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --wait) [ -n "${2:-}" ] || { echo "NO-DATA: --wait needs SECONDS"; exit 2; }; WAIT="$2"; shift 2 ;;
    *) break ;;
  esac
done
case "$WAIT" in
  ''|*[!0-9]*) echo "NO-DATA: --wait SECONDS must be a whole number, not '$WAIT'"; exit 2 ;;
esac
USAGE="usage: merge_verified.sh [--dry-run] [--wait SECONDS] <list>"
LIST="${1:-}"
[ -n "$LIST" ] || { echo "NO-DATA: no list given"; echo "$USAGE"; exit 2; }
# F7, review 2026-10-06: `<list> --dry-run` read the list, never the flag, and was a REAL run. The list is the last
# argument; one more of any kind is refused here, before the first git, gh or python call.
[ $# -eq 1 ] || { echo "NO-DATA: '$2' comes after the list '$LIST'; flags go before the list, so nothing ran"; echo "$USAGE"; exit 2; }
[ -r "$LIST" ] || { echo "NO-DATA: no list at $LIST"; exit 2; }

CLONE="${MERGE_CLONE:-$(cd "$HERE/.." && git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$CLONE" ] || { echo "NO-DATA: cannot find the clone; set MERGE_CLONE"; exit 2; }
REMOTE="${MERGE_REMOTE:-hub}"
BRANCH="${MERGE_BASE_BRANCH:-main}"
REPO="${MERGE_REPO:-$(cd "$CLONE" && git config --get "remote.$REMOTE.url" 2>/dev/null || true)}"
case "$REPO" in
  *github.com[:/]*) REPO="${REPO#*github.com}"; REPO="${REPO#:}"; REPO="${REPO#/}"; REPO="${REPO%.git}" ;;
esac
[ -n "$REPO" ] || { echo "NO-DATA: cannot read the $REMOTE remote; set MERGE_REPO"; exit 2; }

# H3: a real run expects the owner at a terminal. This stops an ACCIDENTAL unattended run (stdin not a terminal); it is
# not authentication: a session can supply a pseudo terminal (script, expect, a pty), and a session of the same user can
# edit this file. Those limits are stated, not solved, here (review rounds 17 and 19, 2026-10-04).
if [ "$DRY" = 0 ] && [ ! -t 0 ]; then
  echo "STOP: a real merge runs only from the owner's terminal; stdin is not a terminal, so this run refuses"
  exit 2
fi

# R-MG-8: a real run refuses to start with any MERGE_GATE_*, MERGE_ALL_*,
# PR_PARK_REPO or git location name set. The guarded process must not be able
# to point the verifier at a ledger and key it controls. After the clean start none of those names is here any more,
# so the rule is handed the names the CALLER carried (data): what was refused before is still refused, by name.
if [ "$DRY" = 0 ]; then
  reason="$(python3 "$PY_GATE" refuse-env "$mv_caller_names" 2>&1)" && guard_rc=0 || guard_rc=$?
  if [ "${guard_rc:-0}" -ne 0 ]; then
    echo "NO-DATA: steered environment refused: $reason"
    exit 2
  fi
fi

# gh, pinned: resolved once and called by absolute path. A real run accepts it only from a system directory, or, in the
# test fixture, from anywhere when the clone carries scripts/.merge-verified-fixture AND its remote is a local path (a
# real hub is never one). HOME must be the account's own: gh reads its identity there.
GH="$GH_FOUND"
if [ "$DRY" = 0 ]; then
  case "$GH" in
    /opt/homebrew/bin/gh|/usr/local/bin/gh|/usr/bin/gh) : ;;
    *) remote_url="$(cd "$CLONE" && git config --get "remote.$REMOTE.url" 2>/dev/null || true)"
       if [ -n "$GH" ] && [ -f "$HERE/.merge-verified-fixture" ] && [ "${remote_url#/}" != "$remote_url" ]; then :
       else echo "STOP: gh at '${GH:-nowhere}' is not a system install (/opt/homebrew/bin, /usr/local/bin or /usr/bin); a real run refuses"; exit 2; fi ;;
  esac
  python3 -c 'import os, pwd, sys; sys.exit(0 if os.environ.get("HOME") == pwd.getpwuid(os.getuid()).pw_dir else 1)' \
    || { echo "STOP: HOME is not this account's own home; a real run refuses"; exit 2; }
fi
gh() { "$GH" "$@"; }

# D7: a real run refuses before its first write while main does not require a
# pull request, or while that answer cannot be read. merge_pins.main_protected
# owns the ONE definition (MG1.d): exit 0 required, 1 not required, 2 unreadable,
# read through the same `gh` this tool already runs. The dry run prints the answer.
main_protected() {
  python3 "$PY_PINS" protected ${GH:+--gh "$GH"} "$REPO" "$BRANCH" >/dev/null 2>&1
}

main_protected; mp=$?
case "$mp" in
  0) echo "PROTECTION: $BRANCH requires a pull request" ;;
  1) echo "PROTECTION: $BRANCH does not require a pull request, so a bare push of main is possible for anyone"
     [ "$DRY" = 1 ] || { echo "STOP: a real run refuses while main requires no pull request"; exit 2; } ;;
  *) echo "PROTECTION: $BRANCH's branch protection cannot be read"
     [ "$DRY" = 1 ] || { echo "STOP: a real run refuses while the protection answer is NO-DATA"; exit 2; } ;;
esac

# The one prompt (R-MG-8, D7): before the FIRST write of a real run the owner types the count of listed PRs on this
# terminal, through merge_pins.owner_confirmed, which reads stdin only (fd 0 here is the terminal; the list is read
# on fd 4 below so the prompt never reads the list). It is an accident stop, not authentication.
n_listed=0
while read -r pr sha rest <&5; do
  case "$pr" in ''|\#*) continue;; esac
  n_listed=$((n_listed+1))
done 5< "$LIST"
confirmed=0
confirm_once() {
  [ "$confirmed" = 1 ] && return 0
  python3 "$PY_PINS" confirm "$n_listed" || {
    echo "STOP: the typed count did not match the $n_listed listed PR(s), or no terminal answered; nothing was written"
    return 1; }
  confirmed=1
}

# read_pr <number> <full>: reads the pull request and checks it is OPEN and based on $BRANCH; with full=1 also
# MERGEABLE and its head equal to the pin $sha of the entry being handled. Sets pr_head and pr_head_ref. A moving
# entry is read here twice: its own OPEN and base before the freeze, then its frozen pull request in full.
read_pr() {
  number="$1"; full="$2"
  info="$(gh pr view "$number" -R "$REPO" --json state,baseRefName,mergeable,headRefOid,headRefName \
          -q '[.state,.baseRefName,.mergeable,.headRefOid,.headRefName]|@tsv' 2>&1)" || {
    echo "STOP at #$number: cannot read it ($info)"; return 1; }
  old_ifs="$IFS"
  IFS="$(printf '\t')"
  set -f
  set -- $info
  set +f
  IFS="$old_ifs"
  [ "$1" = OPEN ] || { echo "STOP at #$number: state is $1"; return 1; }
  [ "$2" = "$BRANCH" ] || { echo "STOP at #$number: base is $2, not $BRANCH"; return 1; }
  pr_head="${4:-}"; pr_head_ref="${5:-}"
  [ "$full" = 1 ] || return 0
  [ "$3" = MERGEABLE ] || { echo "STOP at #$number: mergeable is $3"; return 1; }
  [ "$4" = "$sha" ] || { echo "STOP at #$number: head pin moved (pin $sha, head $4)"; return 1; }
  return 0
}

base_sha="$(cd "$CLONE" && git rev-parse "refs/remotes/$REMOTE/$BRANCH" 2>/dev/null || true)"
[ -n "$base_sha" ] || base_sha="$(cd "$CLONE" && git rev-parse "refs/heads/$BRANCH" 2>/dev/null || true)"
[ -n "$base_sha" ] || { echo "NO-DATA: cannot read $REMOTE/$BRANCH; fetch it first"; exit 2; }

# check_pin: the pin check of the entry in $pr $sha $pr_head_ref $pin $frozen, through merge_pins.check_pins. Its
# answer is read from STDOUT ALONE (a stderr line from a tool underneath, git's own cache warning in a sandbox, is
# never part of it) and classified on its EXACT first token: STATIC, WILL-FREEZE (a moving head with no pin yet:
# PIN-PENDING, not a refusal), PINNED (pull request pending) or FROZEN. A pin branch at another sha or not this
# entry's own, a frozen pull request with another head, a force pushed moving ref, or a remote that cannot be read
# stop here. Sets kind and pin_line.
check_pin() {
  pin_line="$(python3 "$PY_PINS" check ${GH:+--gh "$GH"} "$CLONE" "$REMOTE" "$pr" "$sha" "$pr_head_ref" \
              ${pin:+"pin=$pin"} ${frozen:+"frozen=$frozen"})" && pins_rc=0 || pins_rc=$?
  case "${pins_rc:-0}" in
    0) : ;;
    1) echo "STOP at #$pr: pin refused: $pin_line"; return 1 ;;
    *) echo "STOP at #$pr: pin check is NO-DATA: $pin_line"; return 1 ;;
  esac
  case "${pin_line%% *}" in
    STATIC) kind=static ;;
    WILL-FREEZE) kind=freeze ;;
    PINNED) kind=pinned ;;
    FROZEN) kind=frozen ;;
    *) echo "STOP at #$pr: the pin check gave no known answer: $pin_line"; return 1 ;;
  esac
  return 0
}

# read_fields: pin= and frozen= from the rest of a list line into pin and frozen, with pathname expansion off
read_fields() {
  pin=""; frozen=""
  set -f
  for tok in $rest; do
    case "$tok" in pin=*) pin="${tok#pin=}" ;; frozen=*) frozen="${tok#frozen=}" ;; esac
  done
  set +f
}

# Every entry's pin check runs BEFORE the first merge (and before any prompt), so a batch whose third entry carries
# a force pushed run line or a moved pin merges nothing rather than stopping with two PRs already on main.
pins_refused=0
while read -r pr sha rest <&6; do
  case "$pr" in ''|\#*) continue;; esac
  [ -n "$sha" ] || { echo "STOP at #$pr: no pinned sha in the list"; pins_refused=1; break; }
  read_fields
  read_pr "$pr" 0 || { pins_refused=1; break; }
  check_pin || { pins_refused=1; break; }
  echo "PIN #$pr: $pin_line"
done 6< "$LIST"
[ "$pins_refused" -eq 0 ] || { echo "DONE: 0 of $n_listed listed PR(s): the pin check refused before any write"; exit 1; }

n=0
done_=0
refused=0
while read -r pr sha rest <&4; do
  case "$pr" in ''|\#*) continue;; esac
  n=$((n+1))
  if [ -z "$sha" ]; then
    echo "STOP at #$pr: no pinned sha in the list"
    refused=1
    break
  fi
  read_fields

  read_pr "$pr" 0 || { refused=1; break; }

  # MG1.d: the pin check again on this entry, right before it is handled (every entry was checked once before the
  # first merge, above); it sets kind from the answer's first token.
  check_pin || { refused=1; break; }
  # the number gh pr merge will name: the entry itself, or its frozen-head pull request (M-MG1-D-9)
  target="$pr"
  case "$kind" in
    static) read_pr "$pr" 1 || { refused=1; break; } ;;
    frozen) target="$frozen"; read_pr "$target" 1 || { refused=1; break; } ;;
  esac

  # the tree id is stdout's first line only: git's own stderr (a cache warning, a hint) never becomes part of it
  tree="$(cd "$CLONE" && git merge-tree --write-tree "$base_sha" "$sha" 2>/dev/null)" || {
    why="$(cd "$CLONE" && git merge-tree --write-tree "$base_sha" "$sha" 2>&1 | tail -1)"
    echo "STOP at #$pr: merge-tree failed ($why)"; refused=1; break; }
  tree="$(printf '%s\n' "$tree" | head -1)"
  # one definition of the gate (H4): with --wait the row is waited for through merge_precompute.wait_ready, which
  # returns verify_tree's own verdict; without it verify_tree answers at once
  if [ "$WAIT" -gt 0 ]; then
    verdict="$(python3 "$PY_PRE" wait "$CLONE" "$tree" "$WAIT" 2>&1)" && gate_rc=0 || gate_rc=$?
  else
    verdict="$(python3 "$PY_GATE" verify "$CLONE" "$tree" 2>&1)" && gate_rc=0 || gate_rc=$?
  fi
  # THE VERDICT LINE, NEVER A WARNING (2026-10-05): stderr is merged so a NO-DATA reason is kept, and the interpreter
  # shim can print its own warning first ("couldn't create cache file ... xcrun_db" in a sandbox), which then stood in
  # front of the gate's answer; the first line carrying a verdict word is the answer, the whole text only if none does
  picked="$(printf '%s\n' "$verdict" | grep -E '^(PASS|FAIL|NO-DATA)' | head -1)"; [ -z "$picked" ] || verdict="$picked"
  case "${gate_rc:-0}" in
    0) : ;;
    1) echo "STOP at #$pr: the tree gate says FAIL: $verdict"; refused=1; break ;;
    *) echo "STOP at #$pr: the tree gate is NO-DATA: $verdict"; refused=1; break ;;
  esac

  if [ "$DRY" = 1 ]; then
    echo "WOULD MERGE #$pr at $sha onto $base_sha (tree $tree) [$pin_line]"
    done_=$((done_+1))
    # Chain the dry run: the next listed PR is judged on the tree this one
    # would produce, never on bare main.
    chained="$(cd "$CLONE" && git commit-tree "$tree" -p "$base_sha" -m "dry-run $pr" 2>/dev/null)" || {
      echo "STOP at #$pr: cannot chain the dry-run tree"; refused=1; break; }
    base_sha="$chained"
    continue
  fi

  # The one prompt, right before the first write of this run (a freeze, a pull request, or a merge).
  confirm_once || { refused=1; break; }

  # MG1.d: a moving head is frozen now, after the prompt and immediately before its merge: the branch is pushed at
  # the pinned sha (never force), the pull request is opened from that branch, and each is noted in the list line
  # in place so a rerun after a crash finds it. The frozen pull request is then read in full, like a static one.
  case "$kind" in
    freeze|pinned)
      if [ "$kind" = freeze ]; then
        pin="$(python3 "$PY_PINS" freeze "$CLONE" "$REMOTE" "$pr" "$sha")" || {
          echo "STOP at #$pr: the pin could not be frozen"; refused=1; break; }
        python3 "$PY_PINS" note "$LIST" "$pr" "pin=$pin" >/dev/null || {
          echo "STOP at #$pr: frozen on $pin but the list line could not be rewritten"; refused=1; break; }
      fi
      frozen="$(python3 "$PY_PINS" open ${GH:+--gh "$GH"} "$REPO" "$pr" "$sha" "$pin")" || {
        echo "STOP at #$pr: the frozen-head pull request could not be opened"; refused=1; break; }
      python3 "$PY_PINS" note "$LIST" "$pr" "pin=$pin" "frozen=$frozen" >/dev/null || {
        echo "STOP at #$pr: pull request #$frozen opened but the list line could not be rewritten"; refused=1; break; }
      echo "FROZEN #$pr at $sha on $pin as #$frozen"
      target="$frozen"
      read_pr "$target" 1 || { refused=1; break; } ;;
  esac

  # The base check: --match-head-commit pins the head, not the base, so hub
  # main is re-read here and must still be the base this tree was computed on.
  (cd "$CLONE" && git fetch --quiet "$REMOTE" "$BRANCH") || {
    echo "STOP at #$pr: cannot re-fetch $REMOTE/$BRANCH"; refused=1; break; }
  fresh="$(cd "$CLONE" && git rev-parse FETCH_HEAD)"
  [ "$fresh" = "$base_sha" ] || {
    echo "STOP at #$pr: base moved under the pin ($base_sha -> $fresh)"; refused=1; break; }

  gh pr merge "$target" -R "$REPO" --merge --match-head-commit "$sha" || {
    echo "STOP at #$pr: merge refused"; refused=1; break; }
  if [ "$target" = "$pr" ]; then
    echo "MERGED #$pr at $sha (tree $tree)"
  else
    echo "MERGED #$pr at $sha (tree $tree) through its frozen-head pull request #$target"
  fi

  # After the merge, the tree main holds is compared with the gated tree. A
  # mismatch is TREE-DRIFT and stops the batch.
  (cd "$CLONE" && git fetch --quiet "$REMOTE" "$BRANCH") || {
    echo "STOP at #$pr: cannot re-read $REMOTE/$BRANCH after the merge"; refused=1; break; }
  now="$(cd "$CLONE" && git rev-parse FETCH_HEAD)"
  nowtree="$(cd "$CLONE" && git rev-parse "$now^{tree}")"
  [ "$nowtree" = "$tree" ] || {
    echo "TREE-DRIFT at #$pr: main now holds $nowtree, the gate passed $tree"; refused=1; break; }
  # MG1.e: a PR counts as merged only when its pinned sha is PROVEN reachable from main (merge_reach.py check); the
  # state field is read only to name a STRANDED PR (merged into a base other than main). STRANDED, TREE-DRIFT and
  # NO-DATA stop the batch.
  merged_state="$(gh pr view "$target" -R "$REPO" --json state -q .state 2>/dev/null || true)"
  reach_out="$(python3 "$PY_REACH" check "$CLONE" "$REMOTE" "$pr" "$sha" "$tree" "${merged_state:-UNKNOWN}" 2>&1)" \
    && reach_rc=0 || reach_rc=$?
  echo "$reach_out"
  [ "${reach_rc:-0}" -eq 0 ] || {
    echo "STOP at #$pr: not proven merged from $REMOTE/$BRANCH (exit $reach_rc)"; refused=1; break; }
  # the next listed PR is computed on the main this merge produced (review round 17: base_sha never advanced, so every
  # batch stopped at its second PR with "base moved under the pin")
  base_sha="$now"
  done_=$((done_+1))
done 4< "$LIST"

case "$DRY" in
  1) verb="would merge" ;;
  *) verb="merged" ;;
esac
echo "DONE: $done_ of $n listed PR(s) $verb"
[ "$refused" -eq 0 ] && [ "$done_" -eq "$n" ]
