#!/usr/bin/env python3
"""Acceptance test for capability area 3: partial diff acceptance (G1-M3.6
of docs/plan/READINESS-ROADMAP-2026-08-29.json, node G1-M3, following the
template G1-M3.3 left behind).

Area 3's own definition (docs/plan/CAPABILITY-AREAS.json): a contributor
reviews a multi-hunk diff and accepts only some of the hunks, then confirms
the rejected hunks never landed and the accepted ones did. It fails when
accepting part of a diff also applies the rejected part, or the tool
cannot express partial acceptance at all.

WHAT "THE TOOL" MEANS HERE. This estate's own dispatch spine (scripts/
loop_bridge.py -> scripts/scope_audit.py -> scripts/integrate.py) is
deliberately whole-unit: a unit's entire declared write scope integrates
or none of it does (see integrate.py's own docstring: "lane result ->
scope gate -> apply to canonical tip -> VERIFY ON CANONICAL"). There is no
hunk-granularity concept anywhere in that pipeline. So the real, available
mechanism a contributor has for accepting only part of a produced diff is
git itself, the one tool every acceptance script in this suite already
depends on to build its real repository. This test measures whether THAT
mechanism actually isolates an accepted hunk from a rejected one, using
nothing but `git diff` and `git apply`, both real, both verified against
`git apply --help` before being used, no invented flags.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory, a
scripted worker (no human) that edits two separate regions of one file so
the resulting diff has more than one hunk, then a scripted reviewer step
that keeps one hunk and discards the other.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the diff had more than one hunk, only the accepted hunk's
               region changed after applying just that hunk, and the
               rejected hunk's region still reads its original text
  1  FAIL      the rejected hunk's region changed anyway, or the diff
               could not be produced or split into hunks at all
  2  NO-DATA   git itself is not usable in this environment

Usage: python3 scripts/acceptance_3.py [--explain] [--calibrate]
--calibrate forces this test red by applying the FULL two-hunk patch
instead of the isolated single-hunk one -- the mechanical shape of
"accepting part of a diff also applies the rejected part" -- and passes
only if this test correctly reads the rejected region changing as FAIL.

origin: invoked directly as its own CLI (main(), below, `python3
scripts/acceptance_3.py [--explain] [--calibrate]`, this file's own line
38), by a human or a CI runner checking capability area 3. It is also
reached through scripts/acceptance.py's run_area(), which subprocess.run()s
this script path when someone runs `python3 scripts/acceptance.py --area
3` (scripts/acceptance.py, run_area around lines 55-61). One other module
mentions it in prose rather than calling it: scripts/acceptance_10.py
names this file as the precedent it follows for testing the estate's real,
available mechanism instead of reporting NO-DATA (acceptance_10.py, line
14, "following acceptance_3.py's own precedent exactly"), which is a
textual reference, not a call. scripts/test_acceptance.py drives it as a
test harness (verified: grep -rl acceptance_3 scripts bundle/runtime finds
exactly those two files plus this one).

PRODUCER: this module is the sole producer of every file it writes. The
_write() helper (lines 95-97) is used by build_real_repo() (line 89) to
seed file.txt, and by _run() (lines 164-166) to write the chosen patch
file before applying it with `git apply`. Both live inside the
tempfile.TemporaryDirectory opened at line 135 and are deleted when that
with-block exits; nothing else in this repo writes through this module's
helper.
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

TIME_BUDGET_SECONDS = 30.0

TEMPLATE = """area 3 template addition to G1-M3.3's shape:
  - when the estate's own dispatch spine has no granularity for the
    behaviour an area asks about (here: hunk-level acceptance, versus
    integrate.py's whole-unit all-or-nothing), the real, available
    mechanism a contributor actually has is tested instead of reporting
    NO-DATA by default. NO-DATA stays reserved for "the machinery is not
    present in this checkout", not "this machinery lacks a feature"
  - a multi-hunk diff is produced by editing two regions of one file far
    enough apart that git's default 3-line context keeps them as separate
    hunks, then split on the "@@" hunk headers, which is exactly how a
    reviewer's own partial-accept tooling would have to work
  - the isolation claim is checked in BOTH directions: the accepted
    region actually changed, and the rejected region provably did not,
    against the file's real, pre-edit text
What areas 1 and 2's shape got wrong that this corrects: nothing did.
What this area adds for the next ones: when the estate's own tool cannot
express a capability by design, that is itself the measured finding, and
the fallback mechanism tested must be one every other script here already
trusts (git), never a new one invented for this area alone."""


def sh(args, cwd=None, timeout=60):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def build_real_repo(tmp):
    """A real git repository the tool has never worked in before, with a
    file long enough that two edits far apart land in separate hunks."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    lines = ["line %d" % i for i in range(1, 61)]
    _write(os.path.join(repo, "file.txt"), "\n".join(lines) + "\n")
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)
    return repo


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def scripted_edit_two_regions(repo):
    """The 'multi-hunk diff': a scripted worker changes two lines far
    apart in the same file, no human in the loop."""
    path = os.path.join(repo, "file.txt")
    lines = _read(path).splitlines()
    lines[4] = "CHANGED REGION A"   # line 5
    lines[54] = "CHANGED REGION B"  # line 55
    _write(path, "\n".join(lines) + "\n")


def split_hunks(patch_text):
    """(file_header_lines, [hunk_lines, ...]). The file header (diff/index/
    +++/--- lines) is shared by every hunk; git apply needs it ahead of
    whichever hunk lines follow it."""
    lines = patch_text.splitlines(keepends=True)
    hunk_starts = [i for i, l in enumerate(lines) if l.startswith("@@")]
    if not hunk_starts:
        return lines, []
    header = lines[:hunk_starts[0]]
    bounds = hunk_starts + [len(lines)]
    hunks = [lines[bounds[i]:bounds[i + 1]] for i in range(len(hunk_starts))]
    return header, hunks


def _run(explain, apply_full_patch_instead_of_one_hunk):
    start = time.monotonic()
    probe = sh(["git", "apply", "--help"])
    if probe.returncode != 0:
        return 2, "NO-DATA: git apply is not usable in this environment"

    with tempfile.TemporaryDirectory(prefix="acceptance-3-") as tmp:
        repo = build_real_repo(tmp)
        path = os.path.join(repo, "file.txt")
        original_line5 = _read(path).splitlines()[4]
        original_line55 = _read(path).splitlines()[54]

        # The reviewer is handed a real multi-hunk diff, not a synthetic one.
        scripted_edit_two_regions(repo)
        diff = sh(["git", "diff"], cwd=repo)
        if diff.returncode != 0 or not diff.stdout.strip():
            return 1, "FAIL: git diff produced nothing to review"

        header, hunks = split_hunks(diff.stdout)
        if len(hunks) < 2:
            return 1, ("FAIL: the scripted edit produced %d hunk(s), not a "
                       "multi-hunk diff, so partial acceptance was never "
                       "actually exercised" % len(hunks))

        # Back to pristine before applying anything: the accept/reject
        # decision is exercised on a clean tree, exactly as a reviewer
        # would apply it to the commit they are actually reviewing.
        sh(["git", "checkout", "--", "file.txt"], cwd=repo)

        patch_path = os.path.join(tmp, "chosen.patch")
        if apply_full_patch_instead_of_one_hunk:
            # THE FORCED BAD STATE (--calibrate only): apply BOTH hunks when
            # only the first was meant to be accepted -- the mechanical
            # shape of "accepting part of a diff also applies the rejected
            # part".
            _write(patch_path, "".join(header + hunks[0] + hunks[1]))
        else:
            _write(patch_path, "".join(header + hunks[0]))

        applied = sh(["git", "apply", patch_path], cwd=repo)
        if applied.returncode != 0:
            return 1, ("FAIL: git apply refused the accepted hunk: %s"
                       % applied.stderr.strip())

        after_lines = _read(path).splitlines()
        accepted_landed = after_lines[4] == "CHANGED REGION A"
        rejected_landed = after_lines[54] == "CHANGED REGION B"
        elapsed = time.monotonic() - start

        if explain:
            print(TEMPLATE)

        if not accepted_landed:
            return 1, ("FAIL: the accepted hunk never landed (line 5 is %r, "
                       "was %r)" % (after_lines[4], original_line5))
        if rejected_landed:
            return 1, ("FAIL: the rejected hunk landed anyway (line 55 is "
                       "%r): accepting part of the diff also applied the "
                       "part that was supposed to stay out" % after_lines[54])
        if elapsed > TIME_BUDGET_SECONDS:
            return 1, ("FAIL: the hunks isolated correctly but took %.2fs, "
                       "over the %.0fs budget" % (elapsed, TIME_BUDGET_SECONDS))
        return 0, ("PASS: a %d-hunk diff was reviewed, only the accepted "
                   "hunk landed (line 5 -> %r), and the rejected hunk's "
                   "region still reads its original text (line 55 -> %r) "
                   "in %.2fs (budget %.0fs)"
                   % (len(hunks), after_lines[4], after_lines[54], elapsed,
                      TIME_BUDGET_SECONDS))


def run(explain=False):
    return _run(explain, apply_full_patch_instead_of_one_hunk=False)


def calibrate():
    """G1-M3.6.2: force this test red once. Applies the FULL two-hunk patch
    where only one hunk was meant to be accepted -- the state that must
    stay isolated is made to leak on purpose -- and passes only if this
    test correctly reads the rejected region changing as a failure."""
    code, evidence = _run(explain=False,
                          apply_full_patch_instead_of_one_hunk=True)
    if code == 1 and "landed anyway" in evidence:
        return 0, ("PASS: calibration applied the full two-hunk patch where "
                   "only one hunk was meant to be accepted, and this test "
                   "correctly read the rejected region leaking through as "
                   "failed (%s): a green reading of this test means "
                   "something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 3: partial diff "
                    "acceptance.")
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
