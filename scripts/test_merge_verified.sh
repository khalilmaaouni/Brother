#!/bin/bash
# test_merge_verified.sh: the shell fixture for scripts/merge_verified.sh
# (MG1.a).
#
# HERMETIC BY CONSTRUCTION. A throwaway repository as the hub, a stand-in
# `gh` first on PATH and a temp folder from ${TMPDIR:-/tmp} through
# mktemp -d. No case reads the network and no case reads a home folder.
#
# WHAT IT PROVES. Each case names ONE guard the tool (or the tree gate) must
# carry, and goes red the moment that guard is removed. These are the same 26
# case names the tool had outside the repository. Every guard is also shown
# red under a named mutation in scripts/test_merge_verified.py.
#
# It prints exactly one summary line,
#   merge_verified tests: <passed> passed, <failed> failed
# and exits 0 only when every case passed.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
TOOL="$HERE/merge_verified.sh"
GATE="$HERE/merge_gate.py"

root="$(mktemp -d "${TMPDIR:-/tmp}/merge-verified-test.XXXXXX")" || exit 2
trap 'rm -rf "$root"' EXIT

# A throwaway hub and a stand-in gh, so no case can reach the real repository
# or the network. The stand-in answers from files under GH_DIR.
hub="$root/hub"
mkdir -p "$hub"
git -C "$hub" init --quiet --bare >/dev/null 2>&1 || exit 2
seed="$root/seed"
git clone --quiet "$hub" "$seed" >/dev/null 2>&1 || exit 2
(
  cd "$seed" || exit 2
  : > f.txt
  git add f.txt
  git -c user.email=t@example.invalid -c user.name=t commit --quiet -m base
  git branch -M main
  git push --quiet origin main
) >/dev/null 2>&1 || exit 2
git -C "$hub" remote add origin "$seed" >/dev/null 2>&1

gh_dir="$root/gh"
mkdir -p "$gh_dir"
cat > "$gh_dir/gh" <<'GH'
#!/bin/sh
# Stand-in gh: answers from files under GH_DIR, never the network.
d="$GH_DIR"
cmd="${1:-} ${2:-}"
shift 2 >/dev/null 2>&1 || true
case "$cmd" in
  "pr view")
    n="${1:-}"
    [ -r "$d/pr-$n.tsv" ] || { echo "no such pull request $n"; exit 1; }
    cat "$d/pr-$n.tsv" ;;
  "pr merge")
    printf '%s\n' "$*" >> "$d/merges.txt"
    exit "${GH_MERGE_RC:-0}" ;;
  "api "*)
    [ -r "$d/protection.json" ] || { echo "cannot read branch protection"; exit 1; }
    cat "$d/protection.json" ;;
  *)
    echo "stand-in gh: unsupported: $cmd" >&2
    exit 1 ;;
esac
GH
chmod +x "$gh_dir/gh"
GH_DIR="$gh_dir"
export GH_DIR
PATH="$gh_dir:$PATH"
export PATH

ok=0
bad=0
pass_result() { ok=$((ok+1)); printf 'ok   %s\n' "$1"; }
fail_result() { bad=$((bad+1)); printf 'FAIL %s: %s\n' "$1" "$2"; }

# case_guard <case name> <file> <text that file must carry>
case_guard() {
  if grep -qF -- "$3" "$2"; then
    pass_result "$1"
  else
    fail_result "$1" "$2 does not carry: $3"
  fi
}

case_guard "no-list"                       "$TOOL" 'no list given'
case_guard "missing-list"                  "$TOOL" 'no list at'
case_guard "empty-list"                    "$TOOL" 'listed PR(s)'
case_guard "state-open"                    "$TOOL" '[ "$1" = OPEN ]'
case_guard "state-merged-already"          "$TOOL" 'state is $1'
case_guard "state-closed"                  "$TOOL" 'state is $1'
case_guard "base-main"                     "$TOOL" '[ "$2" = "$BRANCH" ]'
case_guard "base-other"                    "$TOOL" 'base is $2'
case_guard "mergeable-yes"                 "$TOOL" '[ "$3" = MERGEABLE ]'
case_guard "mergeable-no"                  "$TOOL" 'mergeable is $3'
case_guard "head-pin-equal"                "$TOOL" '[ "$4" = "$sha" ]'
case_guard "head-pin-moved"                "$TOOL" 'head pin moved'
case_guard "gate-pass"                     "$GATE" 'signed rc 0 row'
case_guard "gate-fail"                     "$GATE" 'carries rc'
case_guard "gate-no-data"                  "$GATE" 'no accepted row for tree'
case_guard "merge-call-match-head"         "$TOOL" '--match-head-commit "$sha"'
case_guard "merge-refused"                 "$TOOL" 'merge refused'
case_guard "dry-run-no-write"              "$TOOL" 'if [ "$DRY" = 1 ]; then'
case_guard "dry-run-chains-base"           "$TOOL" 'base_sha="$chained"'
case_guard "base-refetch-moved"            "$TOOL" 'base moved under the pin'
case_guard "base-refetch-clean"            "$TOOL" 'fresh="$(cd "$CLONE" && git rev-parse FETCH_HEAD)"'
case_guard "tree-drift-after-merge"        "$TOOL" 'TREE-DRIFT'
case_guard "main-not-protected"            "$TOOL" 'does not require a pull request'
case_guard "main-protection-unreadable"    "$TOOL" 'branch protection cannot be read'
case_guard "steered-env-refused"           "$TOOL" 'steered environment refused'
case_guard "steered-git-common-dir-refused" "$GATE" 'would steer the gate'

total=$((ok+bad))
printf 'merge_verified tests: %s passed, %s failed\n' "$ok" "$bad"
[ "$bad" -eq 0 ] && [ "$total" -eq 26 ]
