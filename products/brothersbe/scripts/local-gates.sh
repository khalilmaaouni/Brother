#!/bin/bash
# Run this repository's real gate battery locally and, on a genuine pass,
# report the result to GitHub as commit statuses.
#
# WHY THIS EXISTS. GitHub Actions is disabled on this estate by founder law of
# 2026-08-16: an eleven job matrix across macOS and Windows consumed a free
# month of minutes in two weeks, and macOS bills at ten times Linux. The
# verification did not need to move to a cloud; only the REPORTING of it did.
# This script keeps the verification exactly where it always ran, on a real
# machine, and posts what it observed.
#
# THE COMMAND LIST IS NOT COPIED. It is extracted from the gates job of
# .github/workflows/brothersbe-gates.yml every run, so the battery here and
# the battery a fork runs under Actions cannot drift apart. A hand-copied list
# would be correct on the day it was written and quietly wrong afterwards.
#
# USAGE
#   scripts/local-gates.sh            run the battery, post statuses
#   scripts/local-gates.sh --no-post  run the battery, report locally only
#
# EXIT 0 only when every extracted command ran and exited 0.
set -u
set -o pipefail   # load-bearing: one gate command ends in `| tee`, and without
                  # this the pipeline reports tee's exit status, so a failing
                  # gate would look green.

cd "$(dirname "$0")/.." || exit 2
GIT_ROOT="$(git rev-parse --show-toplevel)" || exit 2
WORKFLOW=".github/workflows/brothersbe-gates.yml"
POST=1
[ "${1:-}" = "--no-post" ] && POST=0

# --- post target, derived from this checkout's own remote -------------------
# THE REPO POSTED AGAINST WAS A LITERAL until this line, "khalilmaaouni/Brothersbe",
# which only worked because GitHub's status API resolves owner/repo case
# insensitively (the real name is khalilmaaouni/BrotherSBE). Worse than the
# case mismatch: a copy of this script living in another repository would run
# its battery against that repository's tree and post the resulting green
# status onto THIS repository's commits, a true measurement filed as a claim
# about code it never read. So the post target is parsed from the checkout's
# own `origin` remote instead, handling the SSH form (git@host:owner/repo.git)
# and the HTTPS form (https://host/owner/repo.git), stripping any .git suffix.
#
# THIS IS NOT THE SAME TRUST DECISION AS THE COMMAND LIST BELOW. The command
# list must come from a ref an outsider cannot write, because it is EXECUTED
# with this user's full shell; the post target is never executed, only used
# as an address, so reading it from the checkout's own remote is correct
# behaviour, not a weakening: a fork posts to its own fork. What still never
# happens is reading either value from a caller supplied environment variable
# or flag: no override on the command list, none on the post target either.
#
# NEVER GUESSED WHEN IT DOES NOT PARSE. No origin remote, or a URL this cannot
# split into owner and repository, leaves POST_TARGET empty on purpose: the
# battery still runs and the receipt still gets written, but the report
# section below refuses to post and says exactly why, naming the URL it could
# not use, rather than posting to a repository nobody named.
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
POST_TARGET=""
POST_TARGET_NOTE="no origin remote; nothing to post to"
if [ -n "$ORIGIN_URL" ]; then
  PARSED="$(printf '%s' "$ORIGIN_URL" | sed -E 's#^(git@|https?://)[^:/]+[:/]##; s#\.git$##')"
  case "$PARSED" in
    */*) POST_TARGET="$PARSED"; POST_TARGET_NOTE="$POST_TARGET (parsed from origin: $ORIGIN_URL)" ;;
    *)   POST_TARGET_NOTE="origin ($ORIGIN_URL) did not parse into owner/repo; nothing to post to" ;;
  esac
fi

# --- readiness artifact (Band C, CI/SCM handshake) ---------------------------
# WHAT A CONSUMER READS INSTEAD OF RE-DERIVING A VERDICT.
# .github/workflows/consumer-check.yml and bitbucket-pipelines.yml both read
# this file rather than re-running or re-grading the battery: the shape
# matches what `lifecycle.reduce_readiness`'s `requiredProof` facts expect
# (src/brothersbe/lifecycle.py), so a caller can feed this straight in
# alongside its own dossierHeadCommit/accountableHuman/noDataPermitted
# without reshaping it. Overridable location (SBE_READINESS_DIR) so
# tools/test_sbe_ci_handshake.py can pin the emitted JSON without writing
# into this checkout's own .sbe/.
READINESS_DIR="${SBE_READINESS_DIR:-.sbe/readiness}"

# Extracted into its own function, called from two places: the real run
# below, and the `--emit-readiness-only` test mode right after this
# definition, which feeds it a fake step summary instead of paying for a
# full battery run.
emit_readiness_artifact() {
  # $1 sha, $2 state (success|failure), $3 path to a file holding one
  # "VERDICT<TAB>check" line per battery step, in the order it ran.
  local sha="$1" state="$2" steps_file="$3"
  mkdir -p "$READINESS_DIR" || return 1
  python3 - "$sha" "$state" "$steps_file" "$READINESS_DIR/${sha}.json" <<'PY'
import datetime, json, sys
sha, state, steps_file, out_path = sys.argv[1:5]
required = []
with open(steps_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.rstrip("\n")
        if not line:
            continue
        verdict, _, check = line.partition("\t")
        required.append({"check": check, "verdict": verdict})
doc = {
    "schemaVersion": "1.0",
    "headCommit": sha,
    "generatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "batteryState": state,
    "requiredProof": required,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
PY
}

# GUARDED TEST MODE. Runs before every refuse-check below (tracked-tree,
# load, trusted-ref fetch, poison scan) on purpose: a test pinning the
# emission shape has none of that machinery's preconditions and should not
# need them. Never reachable by an ordinary invocation, because an ordinary
# invocation's $1 is unset or `--no-post`.
if [ "${1:-}" = "--emit-readiness-only" ]; then
  TEST_STATE="${2:?usage: local-gates.sh --emit-readiness-only <success|failure> <steps-file>}"
  TEST_STEPS="${3:?usage: local-gates.sh --emit-readiness-only <success|failure> <steps-file>}"
  TEST_SHA="$(git rev-parse HEAD)" || exit 2
  emit_readiness_artifact "$TEST_SHA" "$TEST_STATE" "$TEST_STEPS"
  exit $?
fi

# --- refuse to measure a tree that is still moving --------------------------
# Tracked modifications only. Untracked files do not change what the commit
# under test contains, and this repository's own tooling keeps live lock and
# task state under .sbe/, so blocking on untracked paths would refuse forever.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "REFUSED: tracked files are modified. Nothing ran and nothing was posted."
  echo "A battery run against a tree you are still editing produces confident"
  echo "wrong signals; commit or stash first."
  exit 2
fi
# --- refuse to measure on a machine that is already saturated ----------------
# The founder's standing rule: at load average 187 the measurements are noise.
# It was a rule a person had to remember, and on 2026-08-17 the same person
# forgot it three times in one session, launching batteries at load 26, 243 and
# 267 and killing each after reading the number it should have read first.
#
# The ceiling is a RATIO against core count rather than the raw 187, because 187
# was one machine's number on 8 cores and a ratio travels while a constant does
# not. 4x is deliberately generous: it permits an ordinary busy laptop and
# refuses only genuine saturation, because a guard that fires on normal work
# gets switched off, and a switched-off guard measures nothing. A saturated
# machine produces timeouts indistinguishable from real failures, and each one
# costs a full battery run to disprove.
CORES="$(sysctl -n hw.ncpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
LOAD1="$(uptime | sed 's/.*averages*: *//' | awk '{print $1}' | tr -d ',')"
MAXLOAD="${SBE_GATE_MAX_LOAD:-$((CORES * 4))}"
LOAD_INT="${LOAD1%%.*}"
[ -z "$LOAD_INT" ] && LOAD_INT=0
if [ "$LOAD_INT" -gt "$MAXLOAD" ] 2>/dev/null; then
  echo "REFUSED: load average is ${LOAD1} on ${CORES} cores, over the ${MAXLOAD} ceiling."
  echo "A battery measured on a saturated machine produces timeouts that look"
  echo "exactly like real failures. Wait for the machine to settle, or set"
  echo "SBE_GATE_MAX_LOAD deliberately if this machine's idle load is this high."
  exit 2
fi

SHA="$(git rev-parse HEAD)"
LOG="${TMPDIR:-/tmp}/local-gates-${SHA:0:12}.log"
: > "$LOG"

# --- derive the battery from the workflow -----------------------------------
# TRUSTED SOURCE ONLY. The battery is read from a trusted upstream ref, never
# from the working tree, whenever one can be found. Parsing the checkout would
# let any pull request add a line to the gates job and have it executed here
# with the maintainer's full user: keychain, ssh keys, tokens, no sandbox.
# GitHub gave fork pull requests an isolated runner with a read-only token; a
# local runner has no such boundary, so the command list must come from a ref
# an outsider cannot write.
#
# NOT AN ENVIRONMENT VARIABLE. This line read
# `TRUSTED_REF="${SBE_TRUSTED_REF:-origin/main}"` until 2026-08-17, and that
# override reopened the exact hole the paragraph above says it closes:
# `SBE_TRUSTED_REF=HEAD scripts/local-gates.sh` makes the battery come from the
# checkout again. Demonstrated rather than reasoned about: a step added to the
# gates job of the working tree reached the executed list as `sh -c '...'`. A
# trust anchor the caller can move is not an anchor, so nothing below reads an
# environment variable, a flag, or a config file the caller can point anywhere.
#
# THE LADDER, fixed and nothing caller supplied, replacing the single constant
# this line used to hold. A single hardcoded name refuses to run at all the
# moment this plugin lives in a tree that calls its trunk something else, or
# has no `origin` yet at all, which is exactly the extraction case this exists
# for. First hit wins, nothing past it is consulted:
#   1. origin/main
#   2. origin/master
#   3. whatever origin/HEAD's symbolic ref points at, if the remote has one
TRUSTED_REF=""
for CAND in origin/main origin/master; do
  if git rev-parse --verify --quiet "$CAND" > /dev/null 2>&1; then
    TRUSTED_REF="$CAND"
    break
  fi
done
if [ -z "$TRUSTED_REF" ]; then
  ORIGIN_HEAD_TARGET="$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -n "$ORIGIN_HEAD_TARGET" ]; then
    CAND="${ORIGIN_HEAD_TARGET#refs/remotes/}"
    if git rev-parse --verify --quiet "$CAND" > /dev/null 2>&1; then
      TRUSTED_REF="$CAND"
    fi
  fi
fi

if [ -z "$TRUSTED_REF" ]; then
  # UNANCHORED. This is the extraction case the ladder above exists for: no
  # origin/main, no origin/master, no origin/HEAD pointer, which is exactly
  # what a brand new clone or a just-extracted subtree looks like before
  # anyone pushes it anywhere. There is no caller supplied value that could
  # move this decision, because there is nothing here for a caller to move:
  # refusing the run outright would make the runner useless on the one case
  # it must survive, and trusting HEAD silently would reopen the exact hole
  # the paragraph above closes, only without an environment variable to name.
  # So neither happens. The battery still runs, but from the WORKING TREE
  # instead of a trusted ref, and posting is forced OFF for this run no
  # matter what was passed on the command line: a battery with no trust
  # anchor has nothing to vouch for the command list it just executed, and a
  # weaker claim is only honest if nothing downstream can dress it up as a
  # stronger one.
  POST=0
  TRUSTED_FRESHNESS="the battery came from the working tree; no status could be posted"
  AHEAD="n/a"
  BEHIND="n/a"
  echo "UNANCHORED: no origin/main, origin/master, or origin/HEAD target found."
  echo "Reading the command list from the working tree instead of a trusted ref."
  echo "Posting is forced off for this run regardless of --no-post."
  if [ -f "$WORKFLOW" ]; then
    WORKFLOW_SOURCE="plugin-local ($WORKFLOW under $(pwd))"
    cp "$WORKFLOW" "${TMPDIR:-/tmp}/trusted-workflow.yml"
  elif [ -f "$GIT_ROOT/$WORKFLOW" ]; then
    WORKFLOW_SOURCE="git-root ($GIT_ROOT/$WORKFLOW)"
    cp "$GIT_ROOT/$WORKFLOW" "${TMPDIR:-/tmp}/trusted-workflow.yml"
  else
    echo "REFUSED: cannot find $WORKFLOW in the working tree at either candidate:"
    echo "  plugin-local: $(pwd)/$WORKFLOW"
    echo "  git root:     $GIT_ROOT/$WORKFLOW"
    exit 2
  fi
  TRUSTED_REF="UNANCHORED"
else
  # FETCH BEFORE TRUSTING IT. The resolved ref is an ordinary local file under
  # .git/refs/remotes, rewritable by `git update-ref` with no network and no
  # authentication, and nothing here ever refreshed it. A stale or hand-moved
  # ref silently supplies a battery missing whatever gates the real trunk
  # added. A failed fetch is not fatal by itself, because running offline is
  # a real case, but it is SAID and it is recorded: a battery read from an
  # unrefreshed ref is a weaker claim and the receipt has to carry which kind
  # it was.
  REMOTE_BRANCH="${TRUSTED_REF#origin/}"
  if git fetch --quiet origin "$REMOTE_BRANCH" 2>/dev/null; then
    TRUSTED_FRESHNESS="fetched from origin at run time"
  else
    TRUSTED_FRESHNESS="NOT FETCHED (offline or refused); the battery came from whatever $TRUSTED_REF already held locally"
    echo "WARNING: could not fetch origin/$REMOTE_BRANCH. $TRUSTED_FRESHNESS"
  fi

  # THE WORKFLOW PATH IS ALSO A LADDER, for the same reason the trust anchor
  # is: this plugin is moving into a monorepo subdirectory, where the
  # workflow it has always read at its own root instead sits at the new
  # repository's root. Two candidates, first hit wins, both read from the
  # trusted ref above so a moved workflow file cannot be planted by anyone
  # who does not also own that ref:
  #   a. plugin-local: the path this script has always used, resolved from
  #      the directory it already changed into (today's standalone layout)
  #   b. git-root: the same relative path, resolved from the repository root
  #      instead (the monorepo layout, where this script's cwd is a
  #      subdirectory but the workflow's dormant source sits at the top)
  # Neither resolving is refused, naming both paths tried, rather than
  # guessed at with a third candidate invented here: that is exactly the kind
  # of silent widening this runner exists to refuse everywhere else in it.
  PREFIX="$(git rev-parse --show-prefix)"
  CANDIDATE_LOCAL="${PREFIX}${WORKFLOW}"
  CANDIDATE_ROOT="${WORKFLOW}"
  if git cat-file -e "${TRUSTED_REF}:${CANDIDATE_LOCAL}" 2>/dev/null; then
    WORKFLOW_RESOLVED_REF="${TRUSTED_REF}:${CANDIDATE_LOCAL}"
    WORKFLOW_SOURCE="plugin-local (${CANDIDATE_LOCAL} at ${TRUSTED_REF})"
  elif git cat-file -e "${TRUSTED_REF}:${CANDIDATE_ROOT}" 2>/dev/null; then
    WORKFLOW_RESOLVED_REF="${TRUSTED_REF}:${CANDIDATE_ROOT}"
    WORKFLOW_SOURCE="git-root (${CANDIDATE_ROOT} at ${TRUSTED_REF})"
  else
    echo "REFUSED: cannot read $WORKFLOW from $TRUSTED_REF at either candidate:"
    echo "  plugin-local: ${TRUSTED_REF}:${CANDIDATE_LOCAL}"
    echo "  git-root:     ${TRUSTED_REF}:${CANDIDATE_ROOT}"
    echo "Fetch first, or this is a genuine miss."
    exit 2
  fi

  # THE BATTERY IS NOT BOUND TO THE SHA IT CERTIFIES, and this measures the
  # gap rather than pretending it is closed. The command list comes from the
  # trusted ref, the commands run against HEAD, and the status posts for
  # HEAD. Measured 2026-08-17: HEAD was 7 commits AHEAD of origin/main, so an
  # OLDER gate list was applied to NEWER code and any gate added in those 7
  # commits did not run.
  #
  # Ahead is REPORTED, not refused: refusing it would make the runner useless
  # on exactly the unpushed work it exists to verify, which is the normal
  # case here. Behind is REFUSED: there the trusted ref is newer than the
  # code under test, so the list can demand gates this commit predates, or
  # omit one it still needs.
  AHEAD="$(git rev-list --count "${TRUSTED_REF}..HEAD" 2>/dev/null || echo '?')"
  BEHIND="$(git rev-list --count "HEAD..${TRUSTED_REF}" 2>/dev/null || echo '?')"
  if [ "$BEHIND" != "0" ] && [ "$BEHIND" != "?" ]; then
    echo "REFUSED: HEAD is $BEHIND commit(s) BEHIND $TRUSTED_REF, so the battery would be"
    echo "read from code newer than the code under test. Rebase or merge first."
    exit 2
  fi
  git show "${WORKFLOW_RESOLVED_REF}" > "${TMPDIR:-/tmp}/trusted-workflow.yml"
fi
CMDS="$(python3 - "${TMPDIR:-/tmp}/trusted-workflow.yml" <<'PY'
import re, sys, pathlib
lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").split("\n")
out, injob = [], False
for ln in lines:
    m = re.match(r'^(\s{2})([A-Za-z0-9_-]+):\s*$', ln)
    if m:
        injob = (m.group(2) == "gates")
        continue
    if injob and ln.strip().startswith("run:"):
        c = ln.split("run:", 1)[1].strip()
        if c and not c.startswith("|"):
            out.append(c)
    elif injob and re.match(r'^\s{8,}(python3|sh|bin/sbe) ', ln):
        out.append(ln.strip())
print("\n".join(out))
PY
)"
TOTAL="$(printf '%s\n' "$CMDS" | grep -c . )"

# Positive control. An extractor that silently matches nothing would otherwise
# report a green battery of zero commands, which is the worst possible lie.
if [ "$TOTAL" -lt 40 ]; then
  echo "REFUSED: extracted only $TOTAL commands from $WORKFLOW."
  echo "That is implausibly few, so the extractor is broken or the workflow"
  echo "changed shape. Fix the extractor rather than posting a status."
  exit 2
fi
# --- refuse a tree carrying an interpreter poison ---------------------------
# The tracked-file guard above cannot see this class: it reads
# `--untracked-files=no` on purpose, and these files are untracked. Demonstrated
# 2026-08-17 in this repository, not reasoned about: a sitecustomize.py holding
# `os._exit(0)` makes every python3 process exit 0 before running a line of the
# code under test. Measured on tools/test_sbe_map.py: ZERO bytes of output and
# exit 0, counted by this runner as a pass. 34 of the 52 gate commands begin
# with python3, so one file turns most of the battery green at once. PYTHONPATH
# is stripped below, but a poison file sitting in the CHECKOUT is on sys.path
# whatever the environment says, so it is refused here as well as stripped.
POISON="$(find . -maxdepth 2 \( -name sitecustomize.py -o -name usercustomize.py -o -name '*.pth' \) \
          -not -path './.git/*' 2>/dev/null | head -5)"
if [ -n "$POISON" ]; then
  echo "REFUSED: the tree carries interpreter startup files, which run before any"
  echo "code under test and can force a green battery:"
  echo "$POISON" | sed 's/^/  /'
  exit 2
fi

# --- build the battery's environment from a committed file ------------------
# DECLARED, NOT INHERITED. Until 2026-08-17 every gate command ran with whatever
# the invoking shell held, so the verdict of the required `local-gates` status
# was a function of (commit, operator) with nothing recording the operator half.
# The measured case: BROTHERSBE_VAULT is set in a personal settings file on this
# machine, so the twelve vault-fed checks in tools/sbe_score.py ran with real
# data here and returned NO-DATA under Actions, whose workflow never set it. The
# SAME COMMIT scored 10 PASS / 2 FAIL / 3 NO-DATA locally and 3 PASS / 0 FAIL /
# 12 NO-DATA in CI. scripts/gates.env carries the policy and its reasoning.
ENV_FILE="scripts/gates.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "REFUSED: $ENV_FILE is missing, so the battery's environment is undeclared."
  exit 2
fi
GATE_ENV=(PATH="$PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" LANG="${LANG:-en_US.UTF-8}")
DECLARED=0
while IFS= read -r kv || [ -n "$kv" ]; do
  case "$kv" in ''|\#*) continue;; esac
  case "$kv" in *=*) ;; *) echo "REFUSED: $ENV_FILE line is not KEY=VALUE: $kv"; exit 2;; esac
  GATE_ENV+=("$kv")
  DECLARED=$((DECLARED + 1))
done < "$ENV_FILE"
STRIPPED=$(( $(env | grep -c .) - 4 ))
[ "$STRIPPED" -lt 0 ] && STRIPPED=0
ENV_SHA="$(shasum -a 256 "$ENV_FILE" | cut -d' ' -f1)"

# --- the battery runs without the keys or the network ------------------------
# scripts/gates.sb denies the network and denies reads of the ssh keys, the gh
# config, the keychains and the vault. This matters more here than in the
# sibling repository: this battery runs 52 commands extracted from a workflow
# file, so the blast radius of one hostile command is the whole list. Actions
# gave a fork pull request an isolated runner and a read-only token; the trusted
# ref now protects WHICH commands run, and this protects what they can reach.
# The runner's own fetch and status POST stay OUTSIDE the wrapper.
SANDBOX=()
SANDBOX_NOTE="NONE (battery ran with this user's full access)"
if [ -f scripts/gates.sb ] && command -v sandbox-exec > /dev/null 2>&1; then
  SANDBOX=(sandbox-exec -f scripts/gates.sb
           -D HOME_SSH="$HOME/.ssh"
           -D HOME_GH="$HOME/.config/gh"
           -D HOME_KEYCHAINS="$HOME/Library/Keychains"
           -D HOME_AWS="$HOME/.aws"
           -D VAULT="${BROTHERSBE_VAULT:-$HOME/Documents/Kay Vault}")
  SANDBOX_NOTE="scripts/gates.sb (no network, no ssh keys, no gh config, no keychain, no vault)"
else
  echo "WARNING: no sandbox. $SANDBOX_NOTE"
fi

echo "battery: $TOTAL commands, extracted from $WORKFLOW"
echo "load:    ${LOAD1} on ${CORES} cores (ceiling ${MAXLOAD})"
echo "sandbox: $SANDBOX_NOTE"
echo "sha:     $SHA"
echo "log:     $LOG"
echo "trusted: $TRUSTED_REF, $TRUSTED_FRESHNESS; HEAD is $AHEAD ahead, $BEHIND behind"
echo "env:     $DECLARED declared from $ENV_FILE, 4 inherited (PATH HOME TMPDIR LANG), ~$STRIPPED stripped"

# --- run it ------------------------------------------------------------------
RAN=0
FAILED=""
START=$SECONDS
while IFS= read -r cmd; do
  [ -z "$cmd" ] && continue
  echo "== $cmd" >> "$LOG"
  # < /dev/null is load-bearing. This loop is fed by a heredoc, so without it
  # every command inherits the REMAINING battery as its stdin, and any command
  # that reads stdin silently eats the commands after it. The loop then ends
  # with no failure recorded and the verdict reports success on a partial run.
  # `env -i ... bash -c` rather than the bare `eval` this used to be. Two
  # reasons, in order of severity. First, it is what applies the declared
  # environment built above, which is the control that stops an inherited
  # PYTHONPATH from turning 34 of these 52 commands green without running them.
  # Second, eval ran each command in THIS shell, so a gate that changed a
  # variable or the working directory leaked into every command after it; a
  # subshell per command means each one starts where the runner says it starts.
  # Shell parsing is still needed and still wanted here, because the extracted
  # battery legitimately contains a pipeline (`| tee`) and two `|| true` forms.
  if "${SANDBOX[@]}" env -i "${GATE_ENV[@]}" bash -c "$cmd" < /dev/null >> "$LOG" 2>&1; then
    RAN=$((RAN + 1))
  else
    # Captured here, in the else branch, because `if` swallows the status and
    # the abort check below needs to tell a signal death from a genuine red.
    LAST_CODE=$?
    FAILED="$cmd"
    break
  fi
done <<EOF
$CMDS
EOF
DURATION=$((SECONDS - START))

# Count check, independent of the failure flag. A partial run is not a pass,
# however it became partial.
if [ -z "$FAILED" ] && [ "$RAN" -ne "$TOTAL" ]; then
  FAILED="only ${RAN} of ${TOTAL} commands ran; the battery did not complete"
fi

if [ -n "$FAILED" ]; then
  STATE="failure"
  DESC="FAILED after ${RAN}/${TOTAL} passed: ${FAILED:0:80}"
  echo "RESULT: $DESC"
else
  STATE="success"
  # HOW MANY OF THESE CAN ACTUALLY FAIL. Founder decision 2026-08-17: keep the
  # commands, fix the number. Three of the extracted commands cannot report a
  # failure however broken the tree is: two replays end in `|| true`, which
  # discards their exit code, and `python3 --version` asserts nothing about this
  # repository at all. A status reading "52/52 commands exit 0" therefore claims
  # about 6 percent more coverage than was bought. Actions ran the identical
  # list, so this is not a regression, which is exactly why it went unsaid: a
  # number wrong in both places looks like agreement.
  #
  # Counted from the extracted list rather than hardcoded, so the figure follows
  # the workflow instead of drifting from it the first time someone edits it.
  CANNOT_FAIL="$(printf '%s\n' "$CMDS" | grep -cE '\|\|[[:space:]]*true[[:space:]]*$|^python3 --version$')"
  REAL=$((RAN - CANNOT_FAIL))
  DESC="${REAL}/${TOTAL} real gates exit 0 (${CANNOT_FAIL} of ${TOTAL} cannot fail by construction), ${DURATION}s, $(uname -sm), $(python3 -V 2>&1), run locally"
  echo "RESULT: all $RAN commands passed in ${DURATION}s"
  echo "        of those, $REAL can actually fail; $CANNOT_FAIL cannot (2 replays ending in || true, and python3 --version)"
fi

# --- a killed run is not a verdict ------------------------------------------
# FOUND BY ITS OWN ARTIFACT, 2026-08-17: a battery killed for load reasons left
# a receipt reading `result: failure`, with nothing but an exit code above 128 to
# distinguish a termination from a genuine red, and the signing step then SIGNED
# it. An abort produced a cryptographically vouched record that reads like a
# verdict, which is worse than no record at all.
#
# The failing command's exit code is not kept per-command here, so the check is
# on the LAST gate command's status, which is what `break` left behind. Anything
# above 128 is a signal death (143 SIGTERM, 137 SIGKILL, 130 SIGINT): no receipt,
# no status, say what happened and stop.
if [ -n "$FAILED" ] && [ "${LAST_CODE:-0}" -gt 128 ]; then
  echo "ABORTED: the battery was killed by signal $((LAST_CODE - 128)) after ${DURATION}s,"
  echo "during: ${FAILED:0:60}"
  echo "No receipt and no status: a terminated run produced no verdict, and a"
  echo "receipt saying 'failure' would be read as one. Re-run when ready."
  exit 2
fi

# --- refuse to report about a tree that moved under the run ------------------
if [ "$(git rev-parse HEAD)" != "$SHA" ]; then
  echo "REFUSED to post: HEAD moved during the run. The result describes $SHA,"
  echo "which is no longer what you have. Re-run on a settled tree."
  exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "REFUSED to post: the run modified tracked files, so the result no longer"
  echo "describes a committed state."
  exit 2
fi

# --- durable receipt ---------------------------------------------------------
# Ported from the BrotherModeUp sibling, which had one while this did not.
# Actions kept a public permanent log; a file in TMPDIR does not survive a
# reboot. The receipt travels with the code, so a green from months ago can
# still be examined, and a status forged straight through `gh` is visible by the
# receipt it does not have. Written on pass AND on fail: a gate that records
# only its wins is a worse record than none.
#
# It carries the ENVIRONMENT and the TRUST ANCHOR, not just the result, because
# the two defects this runner shipped were both invisible in a bare verdict: a
# battery that ran under an undeclared environment, and a command list read from
# a ref nobody had refreshed.
mkdir -p evidence/gates
cat > "evidence/gates/${SHA:0:12}.txt" <<RECEIPT
sha:          $SHA
result:       $STATE
ran:          $RAN of $TOTAL extracted command(s)
cannot_fail:  ${CANNOT_FAIL:-unmeasured} of $TOTAL discard their exit code or assert nothing about this repository
failed_at:    ${FAILED:-none}
duration_s:   $DURATION
host:         $(uname -sm)
python:       $(python3 -V 2>&1)
battery_from: $TRUSTED_REF ($TRUSTED_FRESHNESS)
workflow_source: $WORKFLOW_SOURCE
head_vs_ref:  $AHEAD ahead, $BEHIND behind
post_target:  $POST_TARGET_NOTE
env_file:     $ENV_FILE sha256=$ENV_SHA
env_declared: $DECLARED variable(s)
env_inherited: PATH HOME TMPDIR LANG
env_stripped: ~$STRIPPED ambient variable(s) removed before the battery ran
ran_by:       local gate runner, scripts/local-gates.sh
ran_at:       $(date -u +%Y-%m-%dT%H:%M:%SZ)
sandbox:      $SANDBOX_NOTE
load_at_start: ${LOAD1} on ${CORES} cores (ceiling ${MAXLOAD})
RECEIPT

# --- readiness artifact -------------------------------------------------------
# Built from the SAME sequential knowledge the receipt above already has, not
# a second measurement: everything before the failure point (or everything,
# on a clean pass) is PASS, the command the battery actually stopped on is
# FAIL, and anything after it that never ran because the battery stopped
# early is NO-DATA. Never invented, never a silent PASS for a step that did
# not execute.
STEPS_FILE="${TMPDIR:-/tmp}/local-gates-steps-${SHA:0:12}.tsv"
: > "$STEPS_FILE"
STEP_I=0
while IFS= read -r cmd; do
  [ -z "$cmd" ] && continue
  STEP_I=$((STEP_I + 1))
  if [ "$STATE" = "success" ] || [ "$STEP_I" -le "$RAN" ]; then
    STEP_VERDICT="PASS"
  elif [ "$STEP_I" -eq "$((RAN + 1))" ]; then
    STEP_VERDICT="FAIL"
  else
    STEP_VERDICT="NO-DATA"
  fi
  printf '%s\t%s\n' "$STEP_VERDICT" "$cmd" >> "$STEPS_FILE"
done <<EOF
$CMDS
EOF
emit_readiness_artifact "$SHA" "$STATE" "$STEPS_FILE"
echo "readiness: $READINESS_DIR/${SHA}.json"

# --- sign the receipt --------------------------------------------------------
# Forging a green `local-gates` status needs only the GitHub token, whose scopes
# here are repo, workflow, gist and user. The receipt makes a forgery visible by
# what it LACKS, which is detection; the signature is a second, different
# credential that never leaves this machine, which is what makes it hard.
#
# Rejected deliberately: a git-notes receipt. Notes are another push-scoped ref,
# forgeable by the same token that forges the status. Ceremony, not identity.
#
# HONEST LIMIT: a malicious process running as this user can read the key and
# sign. This defeats a leaked-token remote forger, not a compromised machine.
# Keeping the code under test away from the key is the sandbox's job above.
#
# Signing failure is NOT fatal and never changes the verdict: an unsigned
# receipt is a weaker record, not a wrong one, and it says so rather than
# failing silently.
GATES_KEY="${GATES_SIGNING_KEY:-$HOME/.ssh/id_ed25519_gates}"
if [ -f "$GATES_KEY" ] && ssh-keygen -Y sign -f "$GATES_KEY" -n gates-receipt -q \
     "evidence/gates/${SHA:0:12}.txt" 2>/dev/null; then
  echo "receipt: evidence/gates/${SHA:0:12}.txt (signed)"
else
  echo "receipt: evidence/gates/${SHA:0:12}.txt (UNSIGNED)"
fi

if [ "$POST" = 0 ]; then
  echo "(--no-post) nothing sent to any host."
  [ "$STATE" = "success" ]
  exit $?
fi

# --- report ------------------------------------------------------------------
# One context only. The old five named runners and interpreters that do not
# run here; a status must not claim a platform it never touched.
#
# WHICH HOST, read from the origin remote rather than assumed (ORIGIN_URL was
# already read at the top of this script, for POST_TARGET; reused here rather
# than asked for a second time). This posted to GitHub unconditionally, so on
# a Bitbucket-hosted clone the run either failed at `gh` or posted a status
# about this commit to a repository that is not the one being verified. The
# battery itself is host-neutral and always was; only the reporting half was
# GitHub-shaped.
case "$ORIGIN_URL" in
  *bitbucket.org*) HOST="bitbucket" ;;
  *github.com*)    HOST="github" ;;
  *)               HOST="unknown" ;;
esac

if [ "$HOST" = "github" ]; then
  if [ -z "$POST_TARGET" ]; then
    echo "REFUSED to post: $POST_TARGET_NOTE"
    echo "The battery result above still stands; it just cannot be filed against a"
    echo "repository this script could not name."
    exit 2
  fi
  gh api -X POST "repos/$POST_TARGET/statuses/$SHA" \
    -f state="$STATE" -f context="local-gates" -f description="$DESC" > /dev/null \
    && echo "posted local-gates=$STATE against $SHA on GitHub" \
    || { echo "POST FAILED: the battery result above still stands, it just was not reported."; exit 2; }
elif [ "$HOST" = "bitbucket" ]; then
  # Bitbucket's equivalent of a commit status is the build status resource.
  # Its states are the three below, not GitHub's, so the value is translated
  # rather than passed through: sending GitHub's vocabulary here is rejected.
  # The credential is an app password or repository access token in
  # BITBUCKET_TOKEN, as user:token; it is never echoed and never written into
  # the receipt. Absent, this prints NO-DATA and keeps the battery's own exit
  # status, because a result that could not be reported is still a result and
  # must not be reported as a failure of the run.
  case "$STATE" in
    success) BB_STATE="SUCCESSFUL" ;;
    failure) BB_STATE="FAILED" ;;
    *)       BB_STATE="STOPPED" ;;
  esac
  BB_SLUG="$(printf '%s' "$ORIGIN_URL" | sed -e 's#.*bitbucket\.org[:/]##' -e 's#\.git$##')"
  # POSTED THROUGH AN ALLOW-LISTED MODULE, not from this shell. The
  # zero-network property bans curl, wget and nc from every shipped shell
  # script and permits a network client only in a Python module allow-listed
  # BY EXACT PATH and documented in SECURITY.md. GitHub's half satisfies that
  # by shelling out to `gh`, an authenticated CLI the operator installed;
  # Bitbucket has no equivalent CLI, so src/brothersbe/bbstatus.py is its
  # counterpart. That module makes ZERO network attempts without a credential
  # and returns NO-DATA naming the remedy, so an unconfigured machine gets a
  # sentence rather than a failure.
  #
  # Its exit status is deliberately NOT this script's: a report that did not
  # land is a failure to report, never a failure of the run, and the battery's
  # own verdict is the last line below regardless.
  python3 "src/brothersbe/bbstatus.py" "$BB_SLUG" "$SHA" "$STATE" "$DESC" || true
else
  echo "NO-DATA: the battery finished $STATE, and nothing was posted because the origin"
  echo "  remote (${ORIGIN_URL:-none}) resolves to neither github.com nor bitbucket.org."
  echo "  That is a failure to report, not a failure of the run."
fi

[ "$STATE" = "success" ]
