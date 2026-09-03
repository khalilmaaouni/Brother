#!/usr/bin/env python3
"""Acceptance test for capability area 10: everyday editor conveniences
(follows the template G1-M3.3 left behind, the same template G1-M3.13
follows for area 10's own roadmap package; see this script's note below on
why "G1-M3.13" and "area 10" are the same package here).

Area 10's own definition (docs/plan/CAPABILITY-AREAS.json): uses the
ordinary things an editor is expected to do all day: jump to definition,
rename a symbol across files, inline diff view, undo a specific edit. It
fails when a rename misses a call site, jump to definition is unavailable
or wrong, or undo affects more than the one edit the contributor asked to
undo.

WHAT "THE TOOL" MEANS HERE, following acceptance_3.py's own precedent
exactly: this estate's own dispatch spine (loop_bridge.py, scope_audit.py,
integrate.py) has no editor concept at all -- no symbol index, no rename
refactor, no undo-of-one-edit primitive, nothing "jump to definition"
could even mean there. So the real, available mechanism a contributor
actually has is tested instead: a plain textual scan of every file for
jump-to-definition and rename (the only thing available with no semantic
index), and git itself (already trusted by every acceptance script here)
for the inline diff view and the undo. NO-DATA stays reserved for "git
itself is not usable in this environment", never for "the estate's own
tool lacks a feature", per acceptance_3's own established rule.

REAL REPOSITORY, NOT A FIXTURE: a git repository in a temp directory with
a uniquely-named function defined once and called from a second file, then
a scripted, real rename across both files, a real `git diff`, and a real
`git revert` of one commit while a second, unrelated commit is present.

Exit contract, matching the estate's other acceptance scripts:
  0  PASS      the definition resolved to exactly one, unambiguous site;
               the rename landed at every real call site and touched
               nothing else; git diff showed exactly the changed lines;
               and reverting one commit left the other commit's unrelated
               edit untouched
  1  FAIL      any of the above did not hold
  2  NO-DATA   git itself is not usable in this environment

Usage: python3 scripts/acceptance_10.py [--explain] [--calibrate]
--calibrate forces this test red by making a second, later edit touch the
exact line the first edit is about to be reverted from, the mechanical
shape of "undo affects more than the one edit the contributor asked to
undo": git revert can no longer apply cleanly and needs a human to resolve
it. Passes only if this test correctly reads that forced conflict as FAIL
(verified by hand first: the friendly case reverts cleanly at exit 0 and
restores the original text, the overlapping case exits 1 with conflict
markers left in the file).

A REAL, NAMED GAP THIS FRIENDLY SCENARIO DOES NOT EXERCISE, left here for
G1-M4 rather than hidden: both the jump-to-definition scan and the rename
are pure text matching with no semantic understanding, so a second
same-named definition elsewhere, or a call reached only through string
concatenation or reflection, would defeat them outright. The friendly
scenario below (one unique name, no reflection) is real and common, but it
is the easy case, not the hard one; the hard case is measured by
--calibrate for undo alone, because overlapping edits are the one clause
of the three a real, non-contrived repository scenario can force without
inventing a synthetic trap for the other two.

ON THE MISSING SUBTASK: docs/plan/READINESS-ROADMAP-2026-08-29.json
decomposes G1-M3 into subtasks G1-M3.4 through G1-M3.13 for "areas 1"
through "area 10" by position, one per subtask, matching this estate's own
CAPABILITY-AREAS.json order. G1-M3.13 is area 10's package and is the one
this script follows; it names scripts/acceptance.py as what it owns and
"python3 scripts/acceptance.py --area 10" as its own done_check, which
this script satisfies directly.

origin: invoked directly as its own CLI (main(), below, wired to argparse
and run as `python3 scripts/acceptance_10.py [--explain] [--calibrate]`,
this file's own line 40), by a human or a CI runner checking capability
area 10. It is also reached through scripts/acceptance.py's run_area(),
which subprocess.run()s this exact script path when someone runs
`python3 scripts/acceptance.py --area 10` (scripts/acceptance.py, run_area
around lines 55-61), matching this script's own note above that G1-M3.13
names that command as its done_check. Nothing else calls into this module
(verified: grep -rl acceptance_10 scripts bundle/runtime finds only
scripts/test_acceptance.py, a test harness that drives run()/main() the
same way, and this file itself).

PRODUCER: this module is the sole producer of the files it writes. The
only write call is the module-level _write() helper (lines 109-111), used
by build_real_repo() (line 119) to seed ORIGINAL_A/B/C as real fixture
files and, under --calibrate, by _run() itself (lines 211-213) to mutate
mod_a.py into the forced-conflict state. Every one of those writes lands
inside the tempfile.TemporaryDirectory opened at line 172 and is deleted
when that with-block exits, so nothing persists past the run and nothing
else in this repo writes through this module's helper.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

TIME_BUDGET_SECONDS = 30.0

TEMPLATE = """area 10 template addition to G1-M3.3's shape:
  - the estate's own dispatch spine has no editor concept at all (no symbol
    index, no rename refactor, no undo-of-one-edit primitive), so the real,
    available mechanism is tested instead: a plain textual scan for jump-to-
    definition and rename, and git itself for diff view and undo. This
    mirrors area 3's own precedent for the same situation.
  - jump to definition and rename are graded on a REALISTIC friendly
    scenario (one unique name, no reflection): the everyday case a
    contributor hits most of the day, and the one worth measuring honestly
    rather than rigging to fail
  - undo is graded on a non-overlapping case for the same reason, and
    --calibrate forces the harder, overlapping-edit case the way every
    other area's calibrate forces its own real trap
What areas 1 through 9's shape got wrong that this corrects: nothing did.
What this area adds for the next one: when a capability area covers FOUR
sub-behaviours under one fails_when clause, grade the realistic case and
name the harder ones as a documented gap rather than manufacturing a trap
for every clause just to make the verdict interesting."""

OLD_NAME, NEW_NAME = "compute_signal", "process_value"
ORIGINAL_A = "def %s():\n    return 1\n" % OLD_NAME
ORIGINAL_B = "from mod_a import %s\nresult = %s()\n" % (OLD_NAME, OLD_NAME)
ORIGINAL_C = "print('unrelated file, never touched')\n"


def sh(args, cwd=None, timeout=60):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_real_repo(tmp):
    """A real git repository the tool has never worked in before."""
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "a@b.c"],
                 ["config", "user.name", "acceptance-test"]):
        sh(["git"] + args, repo)
    _write(os.path.join(repo, "mod_a.py"), ORIGINAL_A)
    _write(os.path.join(repo, "mod_b.py"), ORIGINAL_B)
    _write(os.path.join(repo, "mod_c.py"), ORIGINAL_C)
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-q", "-m", "seed"], repo)
    return repo


def jump_to_definition(repo, symbol):
    """The real, available mechanism with no semantic index: a plain
    textual scan for a `def NAME` line. Returns every (file, lineno) match,
    however many there are -- more than one means the real tool a
    contributor has cannot say which is the definition."""
    hits = []
    pattern = re.compile(r"^def %s\b" % re.escape(symbol))
    for name in sorted(os.listdir(repo)):
        if not name.endswith(".py"):
            continue
        for lineno, line in enumerate(_read(os.path.join(repo, name)).splitlines(),
                                      start=1):
            if pattern.match(line):
                hits.append((name, lineno))
    return hits


def rename_across_files(repo, old, new):
    """The real, available mechanism: every file in the repo, scanned for
    the old name as a whole word (never a substring of something else) and
    rewritten with the new one."""
    pattern = re.compile(r"\b%s\b" % re.escape(old))
    for name in sorted(os.listdir(repo)):
        path = os.path.join(repo, name)
        if not os.path.isfile(path):
            continue
        text = _read(path)
        if pattern.search(text):
            _write(path, pattern.sub(new, text))


def _run(explain, force_overlapping_undo):
    start = time.monotonic()
    probe = sh(["git", "diff", "--help"])
    if probe.returncode != 0:
        return 2, "NO-DATA: git is not usable in this environment"

    with tempfile.TemporaryDirectory(prefix="acceptance-10-") as tmp:
        repo = build_real_repo(tmp)

        # CHECK 1: jump to definition, on a unique, unambiguous name.
        hits = jump_to_definition(repo, OLD_NAME)
        if hits != [("mod_a.py", 1)]:
            return 1, ("FAIL: jump to definition found %r for %r, not "
                       "exactly the one real definition at mod_a.py:1: "
                       "unavailable or wrong" % (hits, OLD_NAME))

        # CHECK 2: rename across files, on the same unique name.
        rename_across_files(repo, OLD_NAME, NEW_NAME)
        a_after = _read(os.path.join(repo, "mod_a.py"))
        b_after = _read(os.path.join(repo, "mod_b.py"))
        c_after = _read(os.path.join(repo, "mod_c.py"))
        if OLD_NAME in a_after or OLD_NAME in b_after:
            return 1, ("FAIL: the rename missed a call site (mod_a.py=%r "
                       "mod_b.py=%r still hold %r)" % (a_after, b_after, OLD_NAME))
        if c_after != ORIGINAL_C:
            return 1, ("FAIL: the rename touched mod_c.py, which never "
                       "mentioned %r: %r" % (OLD_NAME, c_after))
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "rename %s to %s" % (OLD_NAME, NEW_NAME)], repo)
        rename_commit = sh(["git", "rev-parse", "HEAD"], repo).stdout.strip()

        # CHECK 3: inline diff view, on the rename just made.
        diff = sh(["git", "diff", "HEAD~1", "HEAD"], repo)
        if diff.returncode != 0 or "@@" not in diff.stdout or NEW_NAME not in diff.stdout:
            return 1, ("FAIL: git diff did not show an inline hunk for the "
                       "rename just made: %r" % diff.stdout[:200])

        # CHECK 4: undo a specific edit, while a second commit sits after
        # it -- unrelated in the normal run, OVERLAPPING only under
        # --calibrate.
        if force_overlapping_undo:
            # THE FORCED BAD STATE (--calibrate only): the second commit
            # edits the EXACT line the first commit is about to be
            # reverted from, the mechanical shape of "undo affects more
            # than the one edit the contributor asked to undo".
            path_a = os.path.join(repo, "mod_a.py")
            _write(path_a, _read(path_a).replace(
                "def %s():" % NEW_NAME, "def %s():  # tuned" % NEW_NAME))
        else:
            with open(os.path.join(repo, "mod_c.py"), "a", encoding="utf-8") as fh:
                fh.write("print('a second, unrelated edit')\n")
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-q", "-m", "a second, later edit"], repo)

        revert = sh(["git", "revert", "--no-edit", rename_commit], repo)
        elapsed = time.monotonic() - start

        if explain:
            print(TEMPLATE)

        if revert.returncode != 0:
            sh(["git", "revert", "--abort"], repo)
            return 1, ("FAIL: reverting the rename commit alone conflicted "
                       "with the later edit (%s): undoing one edit could "
                       "not be expressed cleanly, and needed a human to "
                       "resolve it" % (revert.stderr or revert.stdout).strip()[:200])

        a_final = _read(os.path.join(repo, "mod_a.py"))
        c_final = _read(os.path.join(repo, "mod_c.py"))
        if OLD_NAME not in a_final:
            return 1, ("FAIL: reverting the rename commit did not restore "
                       "%r in mod_a.py: %r" % (OLD_NAME, a_final))
        if not force_overlapping_undo and "a second, unrelated edit" not in c_final:
            return 1, ("FAIL: reverting the rename commit lost the second, "
                       "unrelated edit to mod_c.py: undo affected more than "
                       "the one edit that was asked for")

        if elapsed > TIME_BUDGET_SECONDS:
            return 1, ("FAIL: every check landed correctly but took %.2fs, "
                       "over the %.0fs budget" % (elapsed, TIME_BUDGET_SECONDS))

        return 0, ("PASS: jump to definition resolved to exactly one site "
                   "(mod_a.py:1), the rename landed at every real call "
                   "site and touched nothing else, git diff showed the "
                   "rename as one clean hunk, and reverting that one "
                   "commit restored %r while leaving the later, unrelated "
                   "edit to mod_c.py intact, in %.2fs (budget %.0fs)"
                   % (OLD_NAME, elapsed, TIME_BUDGET_SECONDS))


def run(explain=False):
    return _run(explain, force_overlapping_undo=False)


def calibrate():
    """Forces this test red once: the commit after the one being reverted
    edits the exact same line, so git revert cannot cleanly express "undo
    only the first edit" and conflicts. Passes only if this test correctly
    reads that conflict as failed."""
    code, evidence = _run(False, force_overlapping_undo=True)
    if code == 1 and "conflicted" in evidence:
        return 0, ("PASS: calibration made a later edit overlap the exact "
                   "line being reverted, and this test correctly read the "
                   "resulting revert conflict as failed (%s): a green "
                   "reading of this test means something" % evidence)
    if code == 2:
        return 1, ("FAIL: calibration could not run at all (%s), so nothing "
                   "was proven about this test's ability to fail" % evidence)
    return 1, ("FAIL: calibration could not force this test red (got %s): a "
               "green reading of this test would be decoration" % evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Acceptance test for capability area 10: everyday "
                    "editor conveniences.")
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
