#!/bin/sh
# The per branch precheck (C2.a): run a unit's done check, the deploy canary and the loop tool parity on a branch tip in
# a scratch worktree under $BROTHER_SCRATCH, and write logs/precheck-<sha>.txt with one PASS, FAIL or NO-DATA row per check.
#
# Usage, from inside the repository: BROTHER_SCRATCH=/abs/dir sh scripts/branch_precheck.sh BRANCH DONE_CHECK
# Exit: 0 every check PASS, 1 any FAIL, 3 any NO-DATA or a refused input. A check that cannot run is NO-DATA, never PASS.
# Test: python3 -B scripts/test_branch_precheck.py
set -u
nl='
'
wt=''

refuse() { echo "branch_precheck: refused: $1" >&2; exit 3; }

[ "$#" -eq 2 ] || refuse "usage: branch_precheck.sh BRANCH DONE_CHECK"
branch=$1
done_check=$2
[ -n "$branch" ] || refuse "an empty branch name"
case $branch in -*) refuse "a branch name starting with a dash" ;; esac

# The grader's done check rule (OK_CMD in scripts/loop/grade_build.py): ONE python3 test command, no chain, no pipe.
OK_CMD='^python3 (-B )?(-m unittest [A-Za-z0-9_.]+( [A-Za-z0-9_.]+)*( -v)?|scripts/(loop/)?test_[A-Za-z0-9_]+\.py( [A-Za-z_][A-Za-z0-9_.]*)*( -v)?)$'
case $done_check in *"$nl"*) refuse "a done check spanning lines" ;; esac
printf '%s\n' "$done_check" | grep -Eq "$OK_CMD" || refuse "the done check is not one python3 test command"

[ -n "${BROTHER_SCRATCH:-}" ] || refuse "BROTHER_SCRATCH is unset; the system temp folder is never used"
case $BROTHER_SCRATCH in /*) ;; *) refuse "BROTHER_SCRATCH is not an absolute path" ;; esac

root=$(git rev-parse --show-toplevel 2>/dev/null) || refuse "not inside a git repository"
cd "$root" || refuse "cannot enter $root"
sha=$(git rev-parse --verify --quiet "$branch^{commit}") || refuse "the branch $branch does not resolve"
printf '%s\n' "$sha" | grep -Eq '^[0-9a-f]{40}$' || refuse "the tip $sha is not a 40 hex sha"

cleanup() {
  if [ -n "$wt" ]; then
    git -C "$root" worktree remove --force "$wt" >/dev/null 2>&1
    rm -rf "$wt"
  fi
  git -C "$root" worktree prune
}
mkdir -p "$BROTHER_SCRATCH" || refuse "cannot create $BROTHER_SCRATCH"
git worktree prune   # an interrupted run's scratch worktree is cleared here
wt=$(mktemp -d "$BROTHER_SCRATCH/precheck.XXXXXX") || { wt=''; refuse "cannot make a scratch folder"; }
trap cleanup EXIT
trap 'exit 3' HUP INT TERM
git worktree add --quiet --detach "$wt" "$sha" >&2 || refuse "cannot add a worktree at $sha"

rows=''
fails=0
nodata=0
check() {  # NAME PRESENT(yes or no) COMMAND...: run the command in the worktree and add its row
  name=$1
  present=$2
  shift 2
  if [ "$present" != yes ]; then
    verdict=NO-DATA; code=none   # a missing test
  else
    (cd "$wt" && "$@") </dev/null >&2
    code=$?
    case $code in
      0) verdict=PASS ;;
      2|126|127) verdict=NO-DATA ;;
      *) verdict=FAIL ;;
    esac
  fi
  rows="${rows}row | $name | $verdict | exit $code$nl"
  case $verdict in FAIL) fails=1 ;; NO-DATA) nodata=1 ;; esac
}

# Does the done check's test exist at this sha? A script path; or a dotted name that is a package, or some prefix of
# which is a module file (pkg.test_mod.Class). A package alone does not make pkg.test_gone present.
present=no
set -f
set -- $done_check
set +f
shift
[ "$1" = -B ] && shift
if [ "$1" = -m ]; then
  p=$(printf '%s' "$3" | tr . /)
  [ -f "$wt/$p/__init__.py" ] && present=yes
  while [ -n "$p" ]; do
    if [ -f "$wt/$p.py" ]; then present=yes; break; fi
    case $p in */*) p=${p%/*} ;; *) p='' ;; esac
  done
elif [ -f "$wt/$1" ]; then
  present=yes
fi
set -f
set -- $done_check
set +f
check done_check "$present" "$@"

present=no
[ -f "$wt/scripts/test_deploy_canary.py" ] && present=yes
check canary "$present" python3 -B scripts/test_deploy_canary.py
present=no
[ -f "$wt/scripts/test_loop_tool_parity.py" ] && present=yes
check parity "$present" python3 -B scripts/test_loop_tool_parity.py

mkdir -p "$root/logs" || refuse "cannot create $root/logs"
record="$root/logs/precheck-$sha.txt"
tmp="$root/logs/.precheck-$sha.$$"
if { printf '%s' "$rows" && printf 'sha | %s\n' "$sha"; } >"$tmp" && mv -f "$tmp" "$record"; then
  echo "$record"
else
  rm -f "$tmp"
  refuse "cannot write $record"
fi
[ "$fails" -eq 0 ] || exit 1
[ "$nodata" -eq 0 ] || exit 3
exit 0
