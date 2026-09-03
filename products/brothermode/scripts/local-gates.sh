#!/bin/bash
# Run this repository's real gate locally and report the result to GitHub as a
# commit status. The sibling of the same script in BrotherSBE, deliberately.
#
# WHY. GitHub Actions is disabled across this estate by founder law of
# 2026-08-16, after an eleven job matrix across macOS and Windows consumed a
# free month of minutes in two weeks. The verification never needed a cloud.
# Only the REPORTING of it did. This runs the gate where it always ran and
# posts what it observed.
#
# THE GATE IS THIS PROJECT'S OWN, not a copy of one. CLAUDE.md documents
# `python3 tools/test_all.py` as the full battery, 8 to 13 minutes, and warns
# that backgrounding it as a harness task dies at exit 144 with no verdict.
# This script runs it in the foreground and waits, which is the shape that
# works.
#
# USAGE
#   scripts/local-gates.sh            run the gate, post the status
#   scripts/local-gates.sh --no-post  run the gate, report locally only
#
# EXIT 0 only when the battery itself exited 0 AND printed its ALL GREEN line.
set -u
set -o pipefail

cd "$(dirname "$0")/.." || exit 2
POST=1
FLOOR=0
for arg in "$@"; do
  case "$arg" in
    --no-post) POST=0 ;;
    # THE 3.9 FLOOR, which is a PROMISE ON THE FRONT PAGE and was tested only in
    # a workflow that no longer fires. Actions ran both ends of the supported
    # range; this machine runs 3.13 and nothing was checking the floor at all.
    # No install and no password were needed to fix that: Apple's Command Line
    # Tools already ship Python 3.9.6 at /usr/bin/python3, beside the newer
    # interpreter on PATH. This flag shims that one to the front of the
    # battery's PATH.
    #
    # Cadence, mirroring the founder decision quoted in the gates workflow (the
    # floor is the BLOCKING leg because it is the promise, the newest
    # interpreter is informational): 3.13 on every run, the floor at release
    # candidates and on any change to dependencies or syntax surface.
    --floor)   FLOOR=1 ;;
    *) echo "usage: local-gates.sh [--no-post] [--floor]"; exit 2 ;;
  esac
done

# Tracked modifications only: untracked files do not change what the commit
# under test contains. A battery run against a tree still being edited
# produces confident wrong signals, which this project has recorded happening
# four times in one night.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "REFUSED: tracked files are modified. Nothing ran and nothing was posted."
  exit 2
fi
SHA="$(git rev-parse HEAD)"
LOG="${TMPDIR:-/tmp}/bm-local-gates-${SHA:0:12}.log"

# One suite at a time is a standing cap in this project, and two batteries in
# one tree produce false failures in both.
if pgrep -f "tools/test_all.py" > /dev/null 2>&1; then
  echo "REFUSED: a battery is already running in this tree. One suite at a time."
  exit 2
fi

# --- refuse to measure on a machine that is already saturated ----------------
# The founder's standing rule: at load average 187 the measurements are noise.
# It was a rule a person had to remember, and on 2026-08-17 the same person
# forgot it three times in one session, launching batteries at load 26, 243 and
# 267 and killing each one after reading the number it should have read first.
# A rule that costs a kill every time it is forgotten belongs in the machine.
#
# The threshold is the ONE-MINUTE average against core count, not the raw 187
# from the incident: 187 was this machine's number on 8 cores, and a ratio
# travels to a machine with a different core count while a constant does not.
# 4x cores is deliberately generous. It permits an ordinary busy laptop and
# refuses only genuine saturation, because a guard that fires on normal work
# gets switched off, and a switched-off guard measures nothing.
#
# Override with BM_GATE_MAX_LOAD for a machine whose idle load is unusual. There
# is no override that means "ignore the load": the number is printed either way,
# and a run started over this refusal is recorded in the receipt as such.
CORES="$(sysctl -n hw.ncpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
LOAD1="$(uptime | sed 's/.*averages*: *//' | awk '{print $1}' | tr -d ',')"
MAXLOAD="${BM_GATE_MAX_LOAD:-$((CORES * 4))}"
LOAD_INT="${LOAD1%%.*}"
[ -z "$LOAD_INT" ] && LOAD_INT=0
if [ "$LOAD_INT" -gt "$MAXLOAD" ] 2>/dev/null; then
  echo "REFUSED: load average is ${LOAD1} on ${CORES} cores, over the ${MAXLOAD} ceiling."
  echo "A battery measured on a saturated machine produces timeouts that look"
  echo "exactly like real failures, and this project has already spent runs"
  echo "chasing them. Wait for the machine to settle, or set BM_GATE_MAX_LOAD"
  echo "deliberately if this machine's idle load is genuinely this high."
  exit 2
fi
echo "load:    ${LOAD1} on ${CORES} cores (ceiling ${MAXLOAD})"

# --- refuse a tree carrying an interpreter poison ---------------------------
# The tracked-file guard above cannot see this class, because the files are
# UNTRACKED and it deliberately ignores untracked files. Demonstrated on the
# sibling repository 2026-08-17, not reasoned about: a sitecustomize.py holding
# `os._exit(0)`, anywhere Python will import it from, makes every python3
# process exit 0 before running a line of the code under test. The measurement
# was zero bytes of output and exit 0 from a suite that normally prints and
# passes. Python imports sitecustomize and usercustomize automatically at
# startup, and a .pth file in a site directory executes any line beginning
# `import`, so all three are refused here rather than merely stripped from the
# environment: PYTHONPATH is stripped below, but a poison file sitting in the
# CHECKOUT ITSELF is on sys.path regardless of PYTHONPATH.
POISON="$(find . -maxdepth 2 \( -name sitecustomize.py -o -name usercustomize.py -o -name '*.pth' \) \
          -not -path './.git/*' 2>/dev/null | head -5)"
if [ -n "$POISON" ]; then
  echo "REFUSED: the tree carries interpreter startup files, which run before"
  echo "any code under test and can force a green battery:"
  echo "$POISON" | sed 's/^/  /'
  echo "Remove them, or if one is legitimate, name it in scripts/gates.env's"
  echo "comments and narrow this check deliberately."
  exit 2
fi

# --- build the battery's environment from a committed file ------------------
# DECLARED, NOT INHERITED. Until 2026-08-17 this ran with whatever the invoking
# shell held, so the verdict of the required status was a function of (commit,
# operator) and nothing recorded the operator half. scripts/gates.env carries
# the whole policy and its reasoning. PATH, HOME, TMPDIR and the locale are
# inherited on purpose and recorded in the receipt: they identify the machine,
# which this design declares rather than reproduces.
ENV_FILE="scripts/gates.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "REFUSED: $ENV_FILE is missing, so the battery's environment is undeclared."
  echo "A verdict from an undeclared environment is a fact about this shell, not"
  echo "about the commit."
  exit 2
fi
GATE_PATH="$PATH"
FLOOR_NOTE="not run (3.13 leg only)"
if [ "$FLOOR" = 1 ]; then
  # A shim directory holding one symlink, prepended to the battery's PATH, so
  # every `python3` the battery invokes resolves to the floor interpreter
  # without touching the outer shell or installing anything.
  if [ ! -x /usr/bin/python3 ]; then
    echo "REFUSED: --floor asked for the 3.9 floor and /usr/bin/python3 is not"
    echo "executable, so the floor could not be tested. A floor run that"
    echo "silently used the newer interpreter would be the worst outcome here."
    exit 2
  fi
  FLOOR_V="$(/usr/bin/python3 -V 2>&1)"
  case "$FLOOR_V" in
    "Python 3.9"*) ;;
    *) echo "REFUSED: /usr/bin/python3 reports '$FLOOR_V', not a 3.9 series."
       echo "The floor this project promises is 3.9; refusing rather than"
       echo "reporting a floor run that tested something else."
       exit 2 ;;
  esac
  mkdir -p "${TMPDIR:-/tmp}/gates-py39"
  ln -sf /usr/bin/python3 "${TMPDIR:-/tmp}/gates-py39/python3"
  GATE_PATH="${TMPDIR:-/tmp}/gates-py39:$PATH"
  FLOOR_NOTE="$FLOOR_V via /usr/bin/python3"
  echo "floor: $FLOOR_NOTE"
fi
GATE_ENV=(PATH="$GATE_PATH" HOME="$HOME" TMPDIR="${TMPDIR:-/tmp}" LANG="${LANG:-en_US.UTF-8}")
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

echo "gate: python3 tools/test_all.py"
echo "sha:  $SHA"
echo "log:  $LOG"
echo "env:  $DECLARED declared from $ENV_FILE, 4 inherited (PATH HOME TMPDIR LANG), ~$STRIPPED stripped"
# --- the battery runs without the keys or the network ------------------------
# scripts/gates.sb denies reads of the ssh keys, the gh config, the keychains
# and the vault, and denies the network outright. See that file for why it is
# allow-default with a denylist rather than the reverse, and for what that
# costs. The runner's own fetch and status POST stay OUTSIDE this wrapper: they
# are the runner's work, not the code under test's.
#
# Absence is not fatal and does not silently downgrade: a missing profile or a
# missing sandbox-exec is SAID, and recorded in the receipt, so a run without
# isolation is never mistaken for one with it.
SANDBOX=()
# THE VAULT DENY PATH IS DECLARED, NOT GUESSED. This used to default to one
# operator's own Obsidian vault by name, which published that private name from
# a tracked file in a public repository. A generic guess would be worse than no
# default at all: it would deny a directory nobody owns while the real vault
# stayed readable, and the receipt would still say "no vault". So an undeclared
# vault is SAID here rather than papered over, the same way a missing
# sandbox-exec is said below. Declare yours once and the deny is exact:
#   export BROTHERMODE_VAULT="$HOME/Documents/<your vault>"
if [ -n "${BROTHERMODE_VAULT:-}" ]; then
  VAULT_DENY="$BROTHERMODE_VAULT"
  VAULT_NOTE="no vault"
else
  VAULT_DENY="$HOME/.brothermode-no-vault-declared"
  VAULT_NOTE="NO VAULT DECLARED, so no vault path was denied (set BROTHERMODE_VAULT)"
fi
SANDBOX_NOTE="NONE (battery ran with this user's full access)"
if [ -f scripts/gates.sb ] && command -v sandbox-exec > /dev/null 2>&1; then
  SANDBOX=(sandbox-exec -f scripts/gates.sb
           -D HOME_SSH="$HOME/.ssh"
           -D HOME_GH="$HOME/.config/gh"
           -D HOME_KEYCHAINS="$HOME/Library/Keychains"
           -D HOME_AWS="$HOME/.aws"
           -D VAULT="$VAULT_DENY")
  SANDBOX_NOTE="scripts/gates.sb (no network, no ssh keys, no gh config, no keychain, $VAULT_NOTE)"
else
  echo "WARNING: no sandbox. $SANDBOX_NOTE"
fi
echo "sandbox: $SANDBOX_NOTE"

START=$SECONDS
"${SANDBOX[@]}" env -i "${GATE_ENV[@]}" python3 tools/test_all.py > "$LOG" 2>&1
CODE=$?
DURATION=$((SECONDS - START))

# Two conditions, not one. An exit code alone has been wrong here before: the
# battery refuses to report green when a suite writes into the checkout, and
# that refusal is a nonzero exit with every suite passing. Requiring the
# ALL GREEN line as well means the status says what the battery said.
GREEN=0
grep -q "ALL GREEN" "$LOG" && GREEN=1
SUMMARY="$(grep -E '^test_all: [0-9]+ tests' "$LOG" | tail -1 | cut -c1-90)"
[ -z "$SUMMARY" ] && SUMMARY="no summary line found in the log"

if [ "$CODE" -eq 0 ] && [ "$GREEN" -eq 1 ]; then
  STATE="success"
  DESC="${SUMMARY}, ${DURATION}s, $(uname -sm), run locally"
  echo "RESULT: green. $SUMMARY"
else
  STATE="failure"
  DESC="exit ${CODE}, ALL GREEN line present=${GREEN}. ${SUMMARY}"
  echo "RESULT: NOT green. exit $CODE, ALL GREEN present=$GREEN"
fi

if [ "$(git rev-parse HEAD)" != "$SHA" ]; then
  echo "REFUSED to post: HEAD moved during the run. The result describes $SHA."
  exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "REFUSED to post: the run modified tracked files, so the result no"
  echo "longer describes a committed state."
  exit 2
fi

# --- a killed run is not a verdict ------------------------------------------
# FOUND BY ITS OWN ARTIFACT, 2026-08-17: this session killed a battery for load
# reasons, the script ran on past the kill to its receipt block, and left a
# receipt reading `result: failure` with nothing but `exit: 143` to say it had
# been terminated rather than genuinely red. The signing step then SIGNED that
# receipt, so an abort produced a cryptographically vouched record that reads
# like a real verdict. A signature on a lie is worse than no signature.
#
# Anything above 128 is a signal death (143 SIGTERM, 137 SIGKILL, 130 SIGINT).
# Such a run wrote no verdict, so it writes no receipt and posts no status: it
# says what happened and exits. This is the one path where writing nothing is
# more honest than writing something.
if [ "$CODE" -gt 128 ]; then
  echo "ABORTED: the battery was killed by signal $((CODE - 128)) after ${DURATION}s."
  echo "No receipt and no status: a run that was terminated produced no verdict,"
  echo "and a receipt saying 'failure' would be read as one. Re-run when ready."
  exit 2
fi

# --- durable receipt -----------------------------------------------------
# Founder decision 2026-08-17. Actions kept public permanent logs; a temp file
# does not. The receipt travels with the code, so a green status from months
# ago can still be examined, and a forged status is visible by the receipt it
# does not have. Written on pass AND fail: a gate that only records its wins
# is a worse record than none.
mkdir -p evidence/gates
cat > "evidence/gates/${SHA:0:12}.txt" <<RECEIPT
sha:        $SHA
result:     $STATE
summary:    $SUMMARY
exit:       $CODE
all_green:  $GREEN
duration_s: $DURATION
host:       $(uname -sm)
python:     $(env -i "${GATE_ENV[@]}" python3 -V 2>&1)
floor_leg:  $FLOOR_NOTE
sandbox:    $SANDBOX_NOTE
env_file:   $ENV_FILE sha256=$ENV_SHA
env_declared: $DECLARED variable(s) from that file
env_inherited: PATH HOME TMPDIR LANG
env_stripped: ~$STRIPPED ambient variable(s) removed before the battery ran
ran_by:     local gate runner, scripts/local-gates.sh
ran_at:     $(date -u +%Y-%m-%dT%H:%M:%SZ)
RECEIPT

# --- sign the receipt --------------------------------------------------------
# WHY A SIGNATURE AND NOT JUST A RECEIPT. Forging a green `local-gates` status
# needs only the GitHub token, whose scopes here are repo, workflow, gist and
# user: anyone holding it can POST success for any commit without running
# anything. The receipt above makes that visible by what it lacks, which is
# DETECTION. A signature is the second, different credential that makes the
# forgery hard: the private half lives only on this machine and is never a push
# credential, so a leaked token cannot produce one.
#
# Rejected alternative, deliberately: a git-notes receipt. Notes are just
# another push-scoped ref, forgeable by the same token that forges the status.
# That is ceremony, not identity.
#
# HONEST LIMIT, and it is a real one: a malicious process running as this user
# can read the key and sign. This defeats a leaked-token remote forger, not a
# compromised machine. Keeping the code under test away from the key is the
# sandbox's job, not this signature's.
#
# Failure to sign is NOT fatal and does not change the verdict: the battery
# result stands on its own, and a receipt without a signature is a weaker record
# rather than a wrong one. It says so out loud instead of failing silently.
GATES_KEY="${GATES_SIGNING_KEY:-$HOME/.ssh/id_ed25519_gates}"
if [ -f "$GATES_KEY" ]; then
  if ssh-keygen -Y sign -f "$GATES_KEY" -n gates-receipt -q \
       "evidence/gates/${SHA:0:12}.txt" 2>/dev/null; then
    echo "receipt: evidence/gates/${SHA:0:12}.txt (signed)"
  else
    echo "receipt: evidence/gates/${SHA:0:12}.txt (UNSIGNED: ssh-keygen -Y sign failed)"
  fi
else
  echo "receipt: evidence/gates/${SHA:0:12}.txt (UNSIGNED: no key at $GATES_KEY)"
fi

# --- report the verdict to whichever host this checkout pushes to -----------
# TWO-HOST LAW (founder order 2026-08-16, amended the same day). Until this
# block existed the posting arm was `gh api` against a hardcoded GitHub
# repository, so an adopter whose origin lives on Bitbucket Cloud got a
# reporting refusal on top of a battery that had just passed. The ENGINE still
# speaks plain git and nothing else, in tools/ and hooks/; this is the RUNNER,
# and reporting a verdict is the runner's own work, not the code under test's.
#
# Routing is on the ORIGIN REMOTE rather than on a flag somebody has to
# remember: the host is a fact about the checkout, and a flag would be one
# more thing to get wrong on the machine where it matters most.
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
HOST_KIND="$(python3 tools/bm_bbstatus.py classify "$ORIGIN_URL" 2>/dev/null || echo unknown)"
echo "host:    $HOST_KIND (from the origin remote)"

# The ADDRESS to post to, parsed from the same remote by the same tool that
# classified it. One parser, already under test, rather than a second one
# written in sed here: this repository's own rule is to verify with the
# control instead of a second reading of the same string. Empty on purpose
# when the URL does not split, and the GitHub arm below refuses on empty.
POST_TARGET=""
POST_TARGET_NOTE="no origin remote; nothing to post to"
if [ -n "$ORIGIN_URL" ]; then
  if POST_TARGET="$(python3 tools/bm_bbstatus.py slug "$ORIGIN_URL" 2>/dev/null)" \
     && [ -n "$POST_TARGET" ]; then
    POST_TARGET_NOTE="$POST_TARGET (parsed from origin: $ORIGIN_URL)"
  else
    POST_TARGET=""
    POST_TARGET_NOTE="origin ($ORIGIN_URL) did not parse into owner/repo; nothing to post to"
  fi
fi

if [ "$POST" = 0 ]; then
  echo "(--no-post) nothing sent to any host."
  [ "$STATE" = "success" ]
  exit $?
fi

case "$HOST_KIND" in
  github)
    # THE TARGET IS THIS CHECKOUT'S OWN ORIGIN, and until 2026-08-26 it was
    # the literal canonical repository name. On the canonical checkout that
    # was invisible. On a FORK it meant a battery that had just read one tree
    # filed its green onto a DIFFERENT tree's commits, or failed trying, and
    # the standing advice was to pass --no-post and give up reporting. That
    # gap was named twice in docs/BITBUCKET.md and closed neither time, and
    # the sibling's PARITY.md recorded it as the one real divergence between
    # the two runners, so it is closed here rather than named a third time.
    #
    # THE POST TARGET IS AN ADDRESS, NEVER EXECUTED, which is why reading it
    # from the checkout is correct behaviour and not a weakening: a fork
    # posts to its own fork. What still never happens is taking it from a
    # flag or an environment variable, the same refusal the battery's own
    # command list makes.
    #
    # NEVER GUESSED. A URL that does not split into owner and repository
    # leaves POST_TARGET empty and this arm refuses, naming the URL it could
    # not use. The exit 2 on that refusal is the SAME nonzero return this arm
    # has always had on a failed POST, which sessions on the canonical
    # checkout read as load bearing, and the sibling's runner refuses at the
    # same point with the same exit.
    if [ -z "$POST_TARGET" ]; then
      echo "REFUSED to post: $POST_TARGET_NOTE"
      echo "The result above still stands, it just cannot be filed against a"
      echo "repository this script could not name."
      exit 2
    fi
    echo "target:  $POST_TARGET_NOTE"
    gh api -X POST "repos/$POST_TARGET/statuses/$SHA" \
      -f state="$STATE" -f context="local-gates" -f description="$DESC" > /dev/null \
      && echo "posted local-gates=$STATE against $SHA" \
      || { echo "POST FAILED: the result above still stands, it just was not reported."; exit 2; }
    ;;
  bitbucket)
    # A reporting failure NEVER changes the gate's own verdict on this arm,
    # and the difference from the GitHub arm above is deliberate rather than
    # sloppy. On GitHub the nonzero return is old habit in this repository and
    # sessions read it. On Bitbucket there is no such habit to preserve, and
    # turning a green battery red because a credential was absent would teach
    # the adopter team to distrust the gate, which is worse than an unreported
    # pass. The verdict is printed either way, and so is the reason.
    python3 tools/bm_bbstatus.py post --url "$ORIGIN_URL" --sha "$SHA" \
      --state "$STATE" --description "$DESC" --context "local-gates" \
      || echo "the result above still stands, it just was not reported."
    ;;
  *)
    echo "host not recognized from the origin remote, so the verdict was"
    echo "not posted anywhere. The result above still stands. Pass --no-post"
    echo "to say so deliberately and silence this line."
    ;;
esac

[ "$STATE" = "success" ]
