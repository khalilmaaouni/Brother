"""test_japanese_threshold: readiness_gate's "japanese-threshold" item, proven
from THIS repository against the real evidence rather than a stale blocker.

WHY THIS EXISTS. readiness_gate.py's japanese-threshold row pointed at this
exact path and its blocker string said "VB2-03 (Japanese-first retrieval
benchmark, BrotherModeUp) has not landed". It landed: BrotherModeUp PR 176
merged tools/bm_vault_jbench.py plus tools/fixtures/japanese-benchmark.json,
a 245-case benchmark across six classes (lexical_only, mixed, kana_alias,
width_variant, dictionary_dependent, negative) with per-class floors, and
bm_vault_jbench.py itself already exits nonzero when a floor is missed. What
was missing was a BROTHER-SIDE witness: this repository had no evidence file
at the path the gate names, so the gate could not see a thing that landed in
a different repository under a different name.

THE PRODUCT BOUNDARY, NOT THE VENDOR UNIT TESTS. This wrapper runs
bm_vault_jbench.py as a black-box subprocess ("run --cases <fixture>"),
exactly the invocation its own --help documents, and parses ITS printed
per-class table. It never imports bm_vault.py or any BrotherModeUp module,
and it never runs BrotherModeUp's own test suite: the evidence is the
product's own CLI output, read the way a user of the tool would read it.

LOCATING THE OTHER REPOSITORY, HONESTLY. BROTHERMODEUP_TOOLS overrides the
lookup when set; otherwise the conventional path is used. Neither is
guessed past: a machine where BOTH bm_vault_jbench.py and
fixtures/japanese-benchmark.json cannot be found under either candidate
reports NO-DATA naming what is missing and exits 2. It never fabricates a
pass. On the machine that wrote this file the conventional checkout was
sitting on an unrelated branch that predates PR 176's merge (the merge
lives on origin/main; the file is real and released there, just not
materialized in that particular working tree today), which is exactly the
NO-DATA case this wrapper is built to report rather than hide.

THE TWO THRESHOLDS THIS WRAPPER CHECKS, why both exist, the numbers. First,
CLASS_FLOORS below mirrors bm_vault_jbench.py's own per-class floors
(lexical_only 0.90, mixed 0.70, kana_alias 0.70, width_variant 0.70,
dictionary_dependent 0.90, negative 0.90) so this wrapper independently
confirms the per-class table it just parsed, rather than trusting the
vendor tool's own exit code alone. Second, OVERALL_THRESHOLD = 0.78: the
245-case fixture splits 108/68/25/24/10/10 across the six classes, so a run
sitting EXACTLY on every per-class floor computes to about 80.4% overall
((108*.90 + 68*.70 + 25*.70 + 24*.70 + 10*.90 + 10*.90) / 245). 0.78 sits
just under that floor-on-floor baseline, so a run that clears every
per-class floor never fails the overall check on rounding alone, while a
real regression that drags the overall rate down still gets caught even if
no single class happens to cross its own floor.

Exit 0 (PASS): every declared class met its floor and the overall rate met
threshold. Exit 1 (FAIL): a class missed its floor, an unknown/undeclared
overall rate, or the overall rate missed threshold. Exit 2 (NO-DATA): the
BrotherModeUp tools could not be located, launched, or read.

Python 3, standard library only. No network.
No em or en dashes anywhere in this file (the long vowel mark in Japanese
fixture text is not one).
"""
import os
import re
import subprocess
import sys

PASS, FAIL, NODATA = 0, 1, 2

#: Mirrors tools/bm_vault_jbench.py's own CLASS_FLOORS (BrotherModeUp PR
#: 176). Duplicated, never imported: this wrapper treats the other
#: repository's tool as a black-box subprocess boundary.
CLASS_FLOORS = {
    "lexical_only": 0.90,
    "mixed": 0.70,
    "kana_alias": 0.70,
    "width_variant": 0.70,
    "dictionary_dependent": 0.90,
    "negative": 0.90,
}

#: See the module docstring's "THE TWO THRESHOLDS" section for how this
#: number was chosen: just under the 80.4% a run sitting exactly on every
#: per-class floor computes to on this fixture's actual 108/68/25/24/10/10
#: class split.
OVERALL_THRESHOLD = 0.78

CONVENTIONAL_TOOLS_DIR = os.path.expanduser("~/Documents/BrotherModeUp/tools")

#: V6 (vault hardening scope, pinned to M4): since the M4 subtree landing the
#: benchmark tool and its fixture live IN THIS REPOSITORY, so a fresh clone
#: with no other checkout present gets a real PASS or FAIL, never NO-DATA.
#: The in-tree path is tried first; the env override still wins for a
#: deliberate redirect, and the conventional external checkout remains a
#: last fallback for pre-M4 clones.
IN_TREE_TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "products", "brothermode", "tools")

PER_CLASS_RE = re.compile(
    r"^\s+(\S+)\s+(?:(\d+)/(\d+) \((\d+)%\)|NO-DATA)", re.MULTILINE)
OVERALL_RE = re.compile(r"^overall:\s+(\d+)/(\d+)", re.MULTILINE)


def find_bmu_tools():
    """(tools_dir, jbench_path, fixture_path) or (None, None, "NO-DATA: ...").

    BROTHERMODEUP_TOOLS overrides the lookup; otherwise the conventional
    path. A candidate counts only when BOTH files it needs are present, so a
    half-installed checkout is reported the same as a missing one rather
    than crashing later on the file that is absent.
    """
    override = os.environ.get("BROTHERMODEUP_TOOLS")
    candidates = (([override] if override else [])
                  + [IN_TREE_TOOLS_DIR, CONVENTIONAL_TOOLS_DIR])
    tried = []
    for cand in candidates:
        if not cand:
            continue
        jbench = os.path.join(cand, "bm_vault_jbench.py")
        fixture = os.path.join(cand, "fixtures", "japanese-benchmark.json")
        tried.append(cand)
        if os.path.isfile(jbench) and os.path.isfile(fixture):
            return cand, jbench, fixture
    where = ("BROTHERMODEUP_TOOLS=%r, conventional %r" % (override, CONVENTIONAL_TOOLS_DIR)
              if override else "conventional %r" % CONVENTIONAL_TOOLS_DIR)
    return None, None, (
        "NO-DATA: bm_vault_jbench.py and fixtures/japanese-benchmark.json not "
        "both found under any candidate (%s); PR 176 merged them into "
        "BrotherModeUp's origin/main, but this machine's checkout of that "
        "repository is not sitting on a branch that contains them right now" % where)


def parse_output(text):
    """({class_name: (hits, total) or None for NO-DATA}, (hits, total) or
    None for overall). Never raises: an output shape this did not expect
    just yields fewer parsed entries, which evaluate() then reports as a
    missing/failing class rather than crashing."""
    per_class = {}
    for m in PER_CLASS_RE.finditer(text):
        cls = m.group(1)
        per_class[cls] = None if m.group(2) is None else (int(m.group(2)), int(m.group(3)))
    om = OVERALL_RE.search(text)
    overall = (int(om.group(1)), int(om.group(2))) if om else None
    return per_class, overall


def evaluate(per_class, overall, floors=CLASS_FLOORS, overall_threshold=OVERALL_THRESHOLD):
    """(ok, [reason, ...]). Pure and side-effect free on purpose: this is the
    function scripts/test_test_japanese_threshold.py drives backwards with a
    doctored floor to prove a FAIL is reachable, without needing a real
    BrotherModeUp checkout to do it."""
    reasons = []
    for cls, floor in floors.items():
        result = per_class.get(cls)
        if result is None:
            reasons.append("%s: NO-DATA in jbench output (0 cases; floor %.0f%%)"
                            % (cls, floor * 100))
            continue
        hits, total = result
        rate = (hits / total) if total else 0.0
        if rate < floor:
            reasons.append("%s: %d/%d (%.1f%%) below floor %.0f%%"
                            % (cls, hits, total, rate * 100, floor * 100))
    if overall is None:
        reasons.append("overall: could not be parsed from jbench output")
    else:
        hits, total = overall
        rate = (hits / total) if total else 0.0
        if rate < overall_threshold:
            reasons.append("overall: %d/%d (%.1f%%) below threshold %.0f%%"
                            % (hits, total, rate * 100, overall_threshold * 100))
    return (not reasons), reasons


def main(argv=None):
    tools_dir, jbench, fixture_or_err = find_bmu_tools()
    if tools_dir is None:
        print(fixture_or_err)
        return NODATA
    fixture = fixture_or_err

    try:
        proc = subprocess.run([sys.executable, jbench, "run", "--cases", fixture],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=180)
    except OSError as e:
        print("NO-DATA: could not launch bm_vault_jbench.py (%s)" % e)
        return NODATA
    except subprocess.TimeoutExpired:
        print("NO-DATA: bm_vault_jbench.py did not finish within 180s")
        return NODATA

    text = proc.stdout.decode("utf-8", errors="replace")
    print(text)

    if proc.returncode == 2:
        print("NO-DATA: bm_vault_jbench.py itself reported NO-DATA (exit 2)")
        return NODATA

    per_class, overall = parse_output(text)
    ok, reasons = evaluate(per_class, overall)
    if not ok:
        for r in reasons:
            print("FAIL: %s" % r)
        print("FAIL exit 1 test_japanese_threshold")
        return FAIL
    print("PASS exit 0 test_japanese_threshold")
    return PASS


if __name__ == "__main__":
    sys.exit(main())
