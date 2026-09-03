#!/usr/bin/env python3
"""Acceptance test for capability area 6: dirty trees and rebases
preserving unrelated changes (G1-M3.9 of docs/plan/READINESS-ROADMAP-
2026-08-29.json, node G1-M3, following the template G1-M3.3 left behind).

Area 6's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
works in a tree with uncommitted, unrelated changes already present, then
rebases or pulls and confirms nothing unrelated was lost or clobbered. It
fails when an unrelated uncommitted change is overwritten, silently
dropped, or the rebase requires manual conflict resolution the tool could
not surface.

THE REAL MACHINERY UNDER TEST is scripts/integrate.py's own dirty-tree
guard (integrate_one): "canonical is integration-only ground... a canonical
tree that is dirty is already a rule violation and integration REFUSES it
rather than working around it." The docstring names exactly why this
matters: on a FAILED revalidation, integrate_one unwinds with `git reset
--hard`, and reset --hard discards uncommitted local modifications to
every tracked file it touches, unrelated ones included. The guard exists
so that unwind never runs against a tree carrying somebody else's
unrelated work.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory with a
real lane branch (a committed, unrelated feature) and a real uncommitted
edit to a different, already-tracked file, standing in for "the tree with
uncommitted, unrelated changes already present."

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      integrate_one refuses to integrate over the dirty tree, the
               unrelated uncommitted edit survives byte-for-byte, and once
               that edit is committed (the contributor resolving their own
               dirty tree, same as the definition's "pulls or rebases"),
               the real integration lands cleanly with nothing lost
  1  FAIL      the unrelated change was altered, dropped, or integration
               proceeded over a dirty tree at all
  2  NO-DATA   scripts/integrate.py is not present in this checkout

Usage: python3 scripts/acceptance_6.py [--explain] [--calibrate]
--calibrate forces this test red by skipping exactly the one guard clause
under test (the dirty-tree check integrate_one runs before merging) and
driving the same real git sequence integrate_one uses (merge, a failing
revalidation, then the same `git reset --hard` unwind) straight through:
the mechanical shape of "an unrelated uncommitted change is overwritten or
silently dropped." Passes only if this test correctly reads the unrelated
file's lost edit as a failure.

origin: invoked directly as its own CLI (main(), below, `python3
scripts/acceptance_6.py [--explain] [--calibrate]`, this file's own line
38), by a human or a CI runner checking capability area 6. It is also
reached through scripts/acceptance.py's run_area() (subprocess.run() of
this script path on `python3 scripts/acceptance.py --area 6`, run_area
around lines 55-61), and through scripts/product_acceptance.py's own
calibration delegation: CALIBRATE_DELEGATES maps area "7" to
"acceptance_6.py" and _calibrate_via_mechanism_twin() subprocess.run()s it
with --calibrate (product_acceptance.py, lines 1093-1094 and 1097-1108),
for the same reason named there: area 7 has no product-path way to
disable the dirty-tree guard this script proves.
scripts/test_acceptance.py also drives it, as a test harness.

PRODUCER: this module is the sole producer of the files it writes. The
_write() helper (lines 82-84) is used by build_scenario() (lines 107, 114,
121) to seed unrelated.txt and feature.txt inside the real git fixture
repo, built inside the tempfile.TemporaryDirectory opened at line 132
(calibrate() opens its own at line 196) and deleted when that with-block
exits; nothing else in this repo writes through this module's helper.
"""
import argparse
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = """area 6 template addition to G1-M3.3's shape:
  - the real risk this area is about is not `git merge` itself (git already
    refuses to merge over a dirty file that the merge would need to touch);
    it is the UNWIND a failed revalidation triggers afterwards (`git reset
    --hard`), which discards uncommitted edits to tracked files with no
    regard for whether they are related to the change at all
  - the guard under test (integrate_one's dirty-tree check) is proven two
    ways: the honest run shows the guard REFUSING and the unrelated edit
    surviving untouched; --calibrate shows what happens to that same edit
    when exactly that guard is skipped, using the same real git commands
    integrate.py itself runs, never a fabricated disaster
  - the definition's "then rebases or pulls" is modelled as the natural
    next step a contributor actually takes: commit or resolve the dirty
    file, then retry. The honest run drives that second attempt too, and
    checks the real integration lands with nothing lost across the whole
    two-step flow, not only that the first attempt was safely refused
What areas 1 through 5's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: a safety property proven by
demonstrating the guarded path AND the unguarded path against the same
scenario is stronger evidence than exercising the guarded path alone."""


def sh(args, cwd=None, timeout=60):
    import subprocess
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


UNRELATED_ORIGINAL = "unrelated original text\n"
UNRELATED_EDIT = "unrelated LOCAL edit, uncommitted, must survive\n"


def build_scenario(tmp):
    """A real canonical repo plus a real lane branch with an unrelated,
    already-tracked file left dirty in canonical's working tree. Returns
    (repo, lane_branch, unrelated_path)."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    unrelated_path = os.path.join(repo, "unrelated.txt")
    _write(unrelated_path, UNRELATED_ORIGINAL)
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)

    # A real lane branch: a committed, unrelated-to-unrelated.txt feature,
    # standing in for the incoming rebase/pull.
    sh(["git", "checkout", "-q", "-b", "lane"], repo)
    _write(os.path.join(repo, "feature.txt"), "feature work\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "lane change"], repo)
    sh(["git", "checkout", "-q", "main"], repo)

    # The dirty tree: an uncommitted edit to unrelated.txt, already present
    # before any integration is attempted.
    _write(unrelated_path, UNRELATED_EDIT)
    return repo, "lane", unrelated_path


def _run(explain):
    sys.path.insert(0, HERE)
    try:
        import integrate
    except ImportError as exc:
        return 2, "NO-DATA: could not import scripts/integrate.py: %s" % exc

    with tempfile.TemporaryDirectory(prefix="acceptance-6-") as tmp:
        repo, lane, unrelated_path = build_scenario(tmp)
        before = integrate._tip(repo)

        unit = {"id": "U1", "done_check": "test -f feature.txt"}
        first = integrate.integrate_one(repo, lane, unit)

        if explain:
            print(TEMPLATE)

        if first["verdict"] != integrate.REFUSED:
            return 1, ("FAIL: integrate_one read verdict=%s on a dirty "
                       "canonical tree, not REFUSED: %s"
                       % (first["verdict"], first.get("reason", "")))
        if _read(unrelated_path) != UNRELATED_EDIT:
            return 1, ("FAIL: the refusal itself altered the unrelated "
                       "uncommitted edit, which should never have been "
                       "touched before anything was even attempted")
        if integrate._tip(repo) != before:
            return 1, "FAIL: canonical's HEAD moved despite the refusal"

        # "Then rebases or pulls": the contributor resolves their own dirty
        # tree (commits the unrelated edit), exactly as a person would, and
        # retries. This must now succeed and lose nothing.
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "commit my unrelated edit"], repo)
        second = integrate.integrate_one(repo, lane, unit)

        if second["verdict"] != integrate.INTEGRATED:
            return 1, ("FAIL: after resolving the dirty tree, integration "
                       "read verdict=%s, not INTEGRATED: %s"
                       % (second["verdict"], second.get("reason", "")))
        if not os.path.isfile(os.path.join(repo, "feature.txt")):
            return 1, "FAIL: integration reported INTEGRATED but feature.txt never landed"
        if _read(unrelated_path) != UNRELATED_EDIT:
            return 1, ("FAIL: the unrelated edit's content changed across the "
                       "full refuse-then-resolve-then-integrate flow")

        return 0, ("PASS: integrate_one REFUSED to integrate over a dirty "
                   "canonical tree, the unrelated uncommitted edit survived "
                   "byte-for-byte and canonical's HEAD did not move, and "
                   "after the contributor committed it the real integration "
                   "landed (feature.txt) with the unrelated edit still intact")


def run(explain=False):
    return _run(explain)


def calibrate():
    """G1-M3.9.2: force this test red once. Skips exactly the dirty-tree
    guard integrate_one enforces and runs the same real git sequence
    (merge, a failing revalidation, the same `git reset --hard` unwind)
    straight through, so the unrelated uncommitted edit is destroyed by the
    unwind, exactly the disaster the guard exists to prevent. Passes only
    if this test correctly reads that loss as a failure."""
    sys.path.insert(0, HERE)
    try:
        import integrate
    except ImportError as exc:
        return 1, ("FAIL: calibration could not run at all (NO-DATA: could "
                   "not import scripts/integrate.py: %s), so nothing was "
                   "proven about this test's ability to fail" % exc)

    with tempfile.TemporaryDirectory(prefix="acceptance-6-calibrate-") as tmp:
        repo, lane, unrelated_path = build_scenario(tmp)
        before = integrate._tip(repo)

        # THE FORCED BAD STATE: the exact real sequence integrate_one runs,
        # minus its dirty-tree guard. A deliberately failing done_check
        # forces the same unwind (`git reset --hard`) a real failed
        # revalidation would trigger.
        merged = integrate._git(["merge", "--no-ff", "--no-edit", lane], repo)
        if merged.returncode != 0:
            integrate._git(["merge", "--abort"], repo)
            return 1, ("FAIL: calibration could not even merge cleanly (%s), "
                       "so nothing was proven about this test's ability to fail"
                       % (merged.stderr or merged.stdout or "").strip()[:200])
        integrate._run_check("false", repo)
        integrate._git(["reset", "--hard", before], repo)

        survived = os.path.isfile(unrelated_path) and _read(unrelated_path) == UNRELATED_EDIT
        if not survived:
            return 0, ("PASS: calibration skipped the dirty-tree guard and "
                       "drove the same merge-then-unwind sequence integrate_one "
                       "uses; the unrelated uncommitted edit was lost exactly "
                       "as this test expects with the guard bypassed, which is "
                       "what makes the real run()'s REFUSED verdict meaningful")
        return 1, ("FAIL: calibration could not force the unrelated edit's "
                   "loss (it survived even with the guard skipped), so "
                   "nothing was proven about this test's ability to fail")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 6: dirty trees "
                    "and rebases preserving unrelated changes.")
    parser.add_argument("--explain", action="store_true",
                        help="also print the template this area leaves behind")
    parser.add_argument("--calibrate", action="store_true",
                        help="prove this test can fail, instead of running it")
    args = parser.parse_args(argv)
    if args.calibrate:
        code, evidence = calibrate()
    else:
        code, evidence = run(explain=args.explain)
    print(evidence)
    return code


if __name__ == "__main__":
    sys.exit(main())
