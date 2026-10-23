#!/bin/sh
# builder_preflight.sh: the five integration lints a builder runs over its lane BEFORE it reports DONE (unit A1, I13).
#
# WHY. Every red gate of the 1.1.6 night was a lint the builder could have run itself: two gates refused for an
# unowned loop file and a silent failure, two pushes for a test that was not hermetic, one landing for deploy parity;
# about 48 minutes of gates, 5.6 of pushes and 18 of loop (docs/plan/seeds/1.1.7/ISSUES-1.1.6.md I13). The five ran
# only in scripts/required_fast.sh and scripts/loop/land_batch.py, after the work was handed over. This runs the same
# five, the same commands, before. The gate rows stay the second line.
#
# usage, from the lane's tree root:
#   sh scripts/builder_preflight.sh --base REV [--only ROW]
# REV is the commit the lane built on. ROW is one of: judges bl-owns silent-failure release-invariant hermetic base-parity.
#
# A LANE NEVER JUDGES ITS OWN JUDGE (security review of A1, 2026-10-19): the first row refuses a range that changes this
# file, scripts/kernel_closure.py, or anything in the import closure of the programs the rows run (the lint tools,
# scripts/close_unit.py, scripts/loop/tool_stamp.py). Past that row every program below is the base's own bytes, so the
# lane's code runs only as the subject, inside the hermetic row's sandbox. A change to a judge lands through the
# session's whole gate and the deploy step (A2), never through its own preflight. scripts/close_unit.py --lane-base runs
# the BASE commit's copy of this file (git show), never the lane's.
#
# THE FIFTH LINT READS THE BASE, NEVER THE LANE'S OWN EDITS (review finding 7): the base must be the deployed
# generation (every deployable scripts/loop file at REV equals the file the deploy stamp declares), so the lane is
# built on what the loop runs. A lane whose own diff changes a loop tool is never refused here: comparing its edits
# with the stamp before the deploy step would refuse every loop tool unit DONE by construction; the deploy step (A2)
# owns that parity. The stamp is $BROTHER_DEPLOY_TARGET/.deploy-stamp.json (default ~/.claude/bin), the variable
# scripts/test_loop_tool_parity.py and scripts/loop/deploy_stamped.py read.
#
# Exit 0 when every row passed; 1 when any row FAILED; 2 when a row could not judge (NO-DATA, never a pass) or the
# usage is wrong. Every row's full output is kept under the printed evidence directory.
set -u

base=""; only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="${2:-}"; shift 2 || { echo "NO-DATA: --base needs a revision"; exit 2; } ;;
    --only) only="${2:-}"; shift 2 || { echo "NO-DATA: --only needs a row name"; exit 2; } ;;
    -h|--help) sed -n '2,/^set -u$/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "NO-DATA: unknown argument $1 (usage: --base REV [--only ROW])"; exit 2 ;;
  esac
done
case "$only" in
  ""|judges|bl-owns|silent-failure|release-invariant|hermetic|base-parity) ;;
  *) echo "NO-DATA: --only $only is not a row (judges bl-owns silent-failure release-invariant hermetic base-parity)"; exit 2 ;;
esac
[ -n "$base" ] || { echo "NO-DATA: no --base: the preflight judges a lane's range and needs the commit it built on"; exit 2; }
case "$base" in
  -*|*[!A-Za-z0-9_./^~-]*) echo "NO-DATA: --base $base is not a revision this tool will read"; exit 2 ;;
esac
[ -f scripts/close_unit.py ] || { echo "NO-DATA: run from the lane's tree root (no scripts/close_unit.py here)"; exit 2; }
base_sha="$(git rev-parse --verify --quiet "$base^{commit}")" || base_sha=""
[ -n "$base_sha" ] || { echo "NO-DATA: --base $base does not name a commit in this repository"; exit 2; }

ev="$(mktemp -d "${TMPDIR:-/tmp}/builder-preflight.XXXXXX")" || { echo "NO-DATA: no evidence directory"; exit 2; }
# THE HELPERS THE ROWS RUN AS PYTHON ARE THE BASE'S BYTES, run with python3 -I (security review, 2026-10-19): neither the
# lane's directory nor the current one is on their sys.path, so no file the lane adds can stand in for a module they load.
judge="$(mktemp -d "${TMPDIR:-/tmp}/builder-preflight-judge.XXXXXX")" || { echo "NO-DATA: no judge directory"; exit 2; }
trap 'rm -rf "$judge"' EXIT
git show "$base_sha:scripts/kernel_closure.py" > "$judge/kernel_closure.py" 2>/dev/null || rm -f "$judge/kernel_closure.py"
git show "$base_sha:scripts/loop/tool_stamp.py" > "$judge/tool_stamp.py" 2>/dev/null || rm -f "$judge/tool_stamp.py"
worst=0; summary=""

row() {
  name="$1"; shift
  if [ -n "$only" ] && [ "$only" != "$name" ]; then return 0; fi
  "$@" > "$ev/$name.txt" 2>&1
  rc=$?
  case "$rc" in
    0) word=PASS ;;
    1) word=FAIL; worst=1 ;;
    *) word=NO-DATA; [ "$worst" -eq 1 ] || worst=2 ;;
  esac
  printf '%-8s %-18s %s\n' "$word" "$name" "$(grep -v '^[[:space:]]*$' "$ev/$name.txt" | tail -1 | cut -c1-200)"
  if [ "$word" != PASS ]; then
    grep -v '^[[:space:]]*$' "$ev/$name.txt" | tail -6 | sed 's/^/           | /'
    summary="$summary $name"
  fi
}

judges() {
  changed() { { git diff --name-only "$base_sha" -- "$@" && git ls-files --others --exclude-standard -- "$@"; } | sort -u; }
  self="$(changed scripts/builder_preflight.sh scripts/kernel_closure.py)" || { echo "NO-DATA: git could not diff the lane"; return 2; }
  if [ -n "$self" ]; then
    echo "$self" | sed 's/^/FAIL judge changed   /'
    echo "FAIL: the lane changes the preflight or its closure tool; it is judged by the session's whole gate, never by itself"
    return 1
  fi
  # no compiled module, bytecode or path hook anywhere in the lane: the closure below reads source, and these load before
  # it or beside it (an extension module wins over a .py of the same name)
  odd="$(changed . | grep -E '\.(so|pyd|pyc|pyo|pth|dylib)$|(^|/)__pycache__/')"
  if [ -n "$odd" ]; then
    echo "$odd" | sed 's/^/FAIL judge changed   /'
    echo "FAIL: the lane adds or changes a compiled module, bytecode or path hook; a judge could load it in place of its source"
    return 1
  fi
  # no new or changed file named like a standard library module in a folder the judges put on sys.path
  shadow="$(changed . | python3 -I -c 'import os, pkgutil, sys, sysconfig
names = set(getattr(sys, "stdlib_module_names", ())) | set(sys.builtin_module_names)
if not getattr(sys, "stdlib_module_names", None):
    std = sysconfig.get_paths()["stdlib"]
    names |= {m.name for m in pkgutil.iter_modules([std, os.path.join(std, "lib-dynload")])}
for p in sys.stdin.read().split():
    d, top = os.path.dirname(p), p.split("/")
    for base in ("", "scripts", "scripts/loop", "products/brothersbe/tools"):
        n = len(base.split("/")) if base else 0
        flat = d == base and os.path.splitext(top[n])[0] in names
        # a folder shadows only as a package: one without __init__.py is a namespace portion the real module outranks
        pkg = (len(top) > n + 1 and "/".join(top[:n]) == base and top[n] in names
               and os.path.isfile(os.path.join(base, top[n], "__init__.py")))
        if flat or pkg:
            print(p)
            break')"
  if [ -n "$shadow" ]; then
    echo "$shadow" | sed 's/^/FAIL judge changed   /'
    echo "FAIL: the lane adds or changes a file named like a standard library module where a judge imports from"
    return 1
  fi
  [ -s "$judge/kernel_closure.py" ] || { echo "NO-DATA: the base $base_sha carries no scripts/kernel_closure.py to derive the judges with"; return 2; }
  set -- $(python3 -I -B "$judge/kernel_closure.py" --roots scripts/close_unit.py,scripts/test_bl_owns_complete.py,products/brothersbe/tools/sbe_score.py,scripts/release_invariant.py,scripts/hermetic_test_check.py,scripts/loop/tool_stamp.py) || { echo "NO-DATA: the judges' closure could not be derived"; return 2; }
  [ $# -gt 0 ] || { echo "NO-DATA: the judges' closure is empty"; return 2; }
  hit="$(changed "$@")" || { echo "NO-DATA: git could not diff the lane"; return 2; }
  if [ -n "$hit" ]; then
    echo "$hit" | sed 's/^/FAIL judge changed   /'
    echo "FAIL: the lane changes $(echo "$hit" | wc -l | tr -d ' ') file(s) its own preflight runs; it lands through the session's whole gate and the deploy step (A2), never through its own preflight"
    return 1
  fi
  echo "PASS: the lane leaves the preflight's $# judge file(s) as the base holds them"
}
row judges judges

# bl-owns: its own exit 2 is SKIPPED (the plan is not in this tree), which is NO-DATA here
row bl-owns python3 scripts/test_bl_owns_complete.py
silent() {
  # sbe_score itself, not scripts/test_loop_report_run.py Lint, which imports the lane's loop tools to run one lint
  said="$(python3 products/brothersbe/tools/sbe_score.py scripts/loop --repo-only --strict 2>&1)"
  line="$(printf '%s\n' "$said" | grep '^silent-failure-lints' | head -1)"
  [ -n "$line" ] || { printf '%s\n' "$said" | tail -3; echo "NO-DATA: sbe_score printed no silent-failure-lints line"; return 2; }
  echo "$line"
  case "$line" in *PASS*) return 0 ;; *) return 1 ;; esac
}
row silent-failure silent
# release invariant: the tool itself, not readiness_gate (which runs it among six items): inside a sandbox readiness's
# "Tenancy leakage zero" item fails for reasons no lane caused, measured 2026-10-19 on A1's own boxed close
row release-invariant python3 scripts/release_invariant.py

hermetic() {
  # every path the lane changed against its base: committed, staged, unstaged and untracked, deletions excluded
  paths="$( { git diff --name-only --diff-filter=ACMRT "$base_sha" -- && git ls-files --others --exclude-standard; } | sort -u)"
  # shellcheck disable=SC2086 (one path per word: the repository's paths carry no blanks, and a blank would refuse below)
  case "$paths" in *" "*) echo "NO-DATA: a changed path carries a blank; name the paths by hand"; return 2 ;; esac
  python3 scripts/hermetic_test_check.py $paths
}
row hermetic hermetic

base_parity() {
  [ -s "$judge/tool_stamp.py" ] || { echo "NO-DATA: the base $base_sha carries no scripts/loop/tool_stamp.py, the deploy's own filter"; return 2; }
  python3 -I -B - "$base_sha" "${BROTHER_DEPLOY_TARGET:-$HOME/.claude/bin}" "$judge/tool_stamp.py" <<'PY'
import hashlib, importlib.util, json, os, subprocess, sys
base, bin_dir = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("tool_stamp_of_the_base", sys.argv[3])
tool_stamp = importlib.util.module_from_spec(spec)   # the deploy's own filter of what reaches the bin, the base's bytes
spec.loader.exec_module(tool_stamp)
stamp = os.path.join(bin_dir, ".deploy-stamp.json")
try:
    with open(stamp, encoding="utf-8") as fh:
        doc = json.load(fh)
except (OSError, ValueError) as exc:
    print("NO-DATA: no readable deploy stamp at %s (%s): the deployed generation is unknown" % (stamp, exc))
    sys.exit(2)
files = doc.get("files") if isinstance(doc, dict) else None
if not isinstance(files, dict) or not files:
    print("NO-DATA: the deploy stamp %s lists no files" % stamp)
    sys.exit(2)
if doc.get("revision") == base:
    print("PASS: the base %s is the stamped revision" % base[:12])
    sys.exit(0)
ls = subprocess.run(["git", "ls-tree", "-r", "-z", "--full-tree", base, "--", "scripts/loop"], capture_output=True)
if ls.returncode != 0:
    print("NO-DATA: git ls-tree of the base refused (exit %d)" % ls.returncode)
    sys.exit(2)
entries = [e.decode("utf-8", "replace") for e in ls.stdout.split(b"\0") if e]
blobs = {}
for e in entries:
    meta, path = e.split("\t", 1)
    blobs[path[len("scripts/loop/"):]] = meta.split()[2]
def deployable(rel):
    parts = rel.split("/")
    if any(tool_stamp._skip(p) for p in parts):
        return False
    return all("/".join(parts[:i + 1] + ["__init__.py"]) in blobs for i in range(len(parts) - 1))
want = sorted(r for r in blobs if deployable(r))
cat = subprocess.run(["git", "cat-file", "--batch"], input="".join(blobs[r] + "\n" for r in want).encode(),
                     capture_output=True)
if cat.returncode != 0:
    print("NO-DATA: git cat-file refused (exit %d)" % cat.returncode)
    sys.exit(2)
out, at, bad = cat.stdout, 0, []
for rel in want:
    nl = out.index(b"\n", at)
    size = int(out[at:nl].split()[2])
    body = out[nl + 1:nl + 1 + size]
    at = nl + 2 + size
    if files.get("bin/" + rel) != hashlib.sha256(body).hexdigest():
        bad.append(rel)
for rel in bad:
    print("FAIL undeployed   scripts/loop/%s at the base differs from the stamp %s" % (rel, str(doc.get("revision"))[:12]))
if bad:
    print("FAIL: the lane's base %s carries %d loop tool(s) the stamp has not deployed: build on the deployed generation "
          "or deploy the base first" % (base[:12], len(bad)))
    sys.exit(1)
print("PASS: the base %s carries the deployed generation (%d loop tool(s) equal the stamp)" % (base[:12], len(want)))
PY
}
row base-parity base_parity

case "$worst" in
  0) rm -rf "$ev"   # the rows above are the whole record of a pass; a refusal keeps its evidence for reading
     echo "PREFLIGHT PASS: the lane over $base_sha passed ${only:-every row}" ;;
  1) echo "PREFLIGHT FAIL:$summary; DONE is refused until they pass; evidence $ev" ;;
  *) echo "PREFLIGHT NO-DATA:$summary could not judge, which is never a pass; evidence $ev" ;;
esac
exit "$worst"
