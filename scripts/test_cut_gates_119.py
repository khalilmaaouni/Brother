#!/usr/bin/env python3
"""RDM1: the cut gates. The README carries the release's section, X01 holds on its host, and a
person other than the walker has re-measured the candidate by hand.

WHAT THIS PROVES. It builds a throwaway release tree and asks cut_preflight.run_all about it,
then judges the verdicts and the exit code. A tree that is good in every way passes; the same
tree with ONE thing wrong is refused. Nothing here looks a symbol up: a row that exists and always
says OK fails, and so does a row that is missing.

THIS IS RDM1'S FROZEN HARNESS, written before any of its code exists (docs/plan/specs/1.1.9/
RDM1.md). The builder never edits it. Every case that is red before green fails today with an
AssertionError, so the base reads FAILED with errors=0.

HOW A ROW IS FOUND. The new rows are found by NAME, case blind: a row of the result list whose name
contains "readme" (RDM1.a), "x01" (RDM1.b, the macOS half) or "measure" (RDM1.c). The good tree
must show that row as OK, so deleting a row cannot pass; the bad tree must show it REFUSED.
Where the spec says "refuses" for a record that is merely missing or unreadable, a NO-DATA row
(exit 2) is accepted as well: both stop the cut, and neither is ever OK.

FROZEN RECORD SHAPES (JT0's records exist; nothing fixes the rest, so the builder reads it here):
  docs/plan/JOURNEY-J1.json and JOURNEY-J2.json under the root being cut, the shape
  scripts/journey/run_journey.py write_record already writes (journey, rows, clean_home, identity,
  candidate_identity, installed_root, walked_unix, reason, stripped_env), PLUS:
    host         "macos" for the record this preflight reads; any other value is refused, and a
                 record stamped "ubuntu" can never stand in for the macOS half
    claude_bin   list of what the walk's HOME held under .claude/bin, or resolved into it; empty
                 means clean. A non empty list refuses. (A record without the field is not
                 asserted here; unknown is the builder's to refuse.)
    walked_by    the seat that walked the journey (JT0's author)
  The candidate is bound the way scripts/go_no_go_119.py already binds it: identity must equal the
  sha256 of the root's bundle/runtime/RUNTIME-MANIFEST.json, and every row of J1 and J2 must be PASS.
  docs/plan/REMEASURE-<version>.json under the root being cut (RDM1.c):
    {"candidate_identity": <the same sha256>, "seat": <who re-measured by hand>,
     "cells": {"<J>/<row id>": {"score": 0|1|2, "evidence": <non empty text>}, ...}}
  It must cover every row of the J1 and J2 records, the seat must not equal walked_by, and the
  candidate must be the one being cut.
  The Linux half of X01 is cut_driver's, read from a run record the dispatch workflow produces. Its
  location is not fixed by the spec, so this file does not test it; see the report.

Hermetic: a scratch HOME, throwaway trees, scripted runner (no network, no real gate runs).
Python 3.9 compatible, standard library only.

    python3 -B scripts/test_cut_gates_119.py StaleReadme
    python3 -B scripts/test_cut_gates_119.py X01
    python3 -B scripts/test_cut_gates_119.py ReMeasure
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

VERSION = "1.1.9"
NOW = datetime.datetime(2026, 10, 29, 15, 0, 0)
MANIFEST = b'{"files": ["brother_run.py"], "note": "the candidate under test"}\n'
IDENTITY = hashlib.sha256(MANIFEST).hexdigest()
GOOD_README = ("# fixture\n\n## What changed in 1.1.8\nthe previous release\n\n"
               "## What changed in 1.1.9\nthis release\n")
MARKET = json.dumps({"plugins": [{"name": "brother", "version": "1.1.8", "source": {"path": "bundle"}}]})
READINESS = json.dumps({"rows": [{"id": "restore-drill", "title": "Restore drill",
                                  "critical": True, "verdict": "PASS"}]})
JOURNEY_ROWS = {"J1": {"A.first-screen-paths-exist": "PASS", "D.six-verbs-only": "PASS"},
                "J2": {"D.end-card-six-lines": "PASS"}}
GOOD = object()      # "build the good one"
ABSENT = None        # "write nothing"


def put(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)


def journey_record(journey, **over):
    """The record JT0 writes for one journey on the macOS host, plus the three frozen fields."""
    rec = {"journey": journey, "host": "macos", "clean_home": True, "identity": IDENTITY,
           "candidate_identity": IDENTITY, "claude_bin": [], "walked_by": "seat-jt0",
           "rows": dict(JOURNEY_ROWS[journey]), "installed_root": "/scratch/installed",
           "walked_unix": 1793268267, "reason": "", "stripped_env": []}
    rec.update(over)
    return rec


def remeasure_record(**over):
    cells = {}
    for journey, rows in sorted(JOURNEY_ROWS.items()):
        for rid in sorted(rows):
            cells["%s/%s" % (journey, rid)] = {"score": 2, "evidence": "walked by hand, notes kept"}
    rec = {"candidate_identity": IDENTITY, "seat": "seat-remeasure", "cells": cells}
    rec.update(over)
    return rec


class Runner(object):
    """The sibling tests' scripted runner: answers by script or git verb, success unless told."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        key = os.path.basename(cmd[1]) if cmd[0] in (sys.executable, "sh") else " ".join(cmd[:2])
        self.calls.append(key)
        out = {"readiness_gate.py": READINESS, "required_fast.sh": "pass 34   fail 0   no-data 3\n",
               "git rev-parse": "abc\n", "git show": MARKET}.get(key, "")
        if list(cmd[:3]) == ["git", "ls-remote", "--tags"]:
            out = "abc\trefs/tags/v1.1.8\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")


class Cut(unittest.TestCase):
    """One throwaway release tree per case, good in every way unless the case spoils one thing."""

    def setUp(self):
        import cut_preflight
        import perturb_pool
        self.P = cut_preflight
        self.tmp = tempfile.mkdtemp(prefix="cut-gates-119-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".claude"))
        for patcher in (mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home}),
                        mock.patch.object(perturb_pool, "free_gib", lambda path: 999.0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tree(self, readme=GOOD, j1=GOOD, j2=GOOD, remeasure=GOOD, manifest=MANIFEST):
        """A release tree for VERSION. Each record argument: GOOD, ABSENT (None), a dict (written as
        JSON) or bytes/str (written raw, for the corrupt cases)."""
        root = tempfile.mkdtemp(prefix="tree-", dir=self.tmp)
        put(os.path.join(root, "docs", "releases", "1.1.8.md"), "Cut from hub commit `9674ed8955ad`\n")
        for name in ("gen_release_plan.py", "version_source.py", "test_version_truth.py"):
            put(os.path.join(root, "scripts", name), "")
        put(os.path.join(root, "bundle", "runtime", "RUNTIME-MANIFEST.json"), manifest)
        if readme is not ABSENT:
            put(os.path.join(root, "README.md"), GOOD_README if readme is GOOD else readme)
        wanted = (("JOURNEY-J1.json", j1, journey_record("J1")),
                  ("JOURNEY-J2.json", j2, journey_record("J2")),
                  ("REMEASURE-%s.json" % VERSION, remeasure, remeasure_record()))
        for name, given, good in wanted:
            if given is ABSENT:
                continue
            body = good if given is GOOD else given
            put(os.path.join(root, "docs", "plan", name),
                body if isinstance(body, (bytes, str)) else json.dumps(body))
        return root

    def preflight(self, root):
        """run_all over `root` with every gate script answered by the scripted runner. The minutes
        long export rows are off (virgin=False); the new rows are cheap file reads and must run
        regardless."""
        def build(dest):
            put(os.path.join(dest, ".claude-plugin", "marketplace.json"), MARKET)
        return self.P.run_all(root, VERSION, remote="https://x", runner=Runner(), now=NOW,
                              build=build, home=self.home, virgin=False)

    def rows(self, results, needle):
        return [r for r in results if needle in r[1].lower()]

    def assertGood(self, results, needle):
        """The control half of every pair: the row exists, reads OK, and the whole cut is clear."""
        found = self.rows(results, needle)
        self.assertTrue(found, "no row named *%s* in %s" % (needle, [r[1] for r in results]))
        self.assertEqual([r[0] for r in found], [self.P.OK] * len(found), found)
        self.assertEqual(self.P.exit_code(results), self.P.EXIT_OK, results)

    def assertRefused(self, results, needle, strict=True):
        """A *needle* row exists and refuses (strict) or at least does not read OK (NO-DATA allowed),
        and the exit code stops the cut."""
        found = self.rows(results, needle)
        self.assertTrue(found, "no row named *%s* in %s" % (needle, [r[1] for r in results]))
        allowed = (self.P.REFUSED,) if strict else (self.P.REFUSED, self.P.NODATA)
        self.assertTrue(any(r[0] in allowed for r in found), "no refusing *%s* row: %s" % (needle, found))
        self.assertNotEqual(self.P.exit_code(results), self.P.EXIT_OK, results)

    def pair(self, needle, bad, strict=True):
        """The good tree reads OK; the spoiled one refuses. Both judged on the same row."""
        self.assertGood(self.preflight(self.tree()), needle)
        self.assertRefused(self.preflight(self.tree(**bad)), needle, strict)


# =====================================================================================
# RDM1.a  StaleReadme
# =====================================================================================

class StaleReadme(Cut):
    """REQ-RDM1A-01. Mutation: M-RDM1A-NO-ROW (the preflight row is dropped)."""

    def test_a_stale_readme_refuses_the_cut(self):
        """The README has the previous release's section and not this one's: the cut is refused on
        a README row. The same tree with the section present is clear. Kills M-RDM1A-NO-ROW."""
        stale = "# fixture\n\n## What changed in 1.1.8\nthe previous release\n"
        self.pair("readme", {"readme": stale})

    def test_a_missing_readme_refuses_the_cut(self):
        self.assertRefused(self.preflight(self.tree(readme=ABSENT)), "readme", strict=False)

    def test_an_unreadable_readme_refuses_the_cut(self):
        """Bytes that are not UTF-8 are never read as 'has the section'."""
        raw = b"\xff\xfe## What changed in 1.1.9\n"
        self.assertRefused(self.preflight(self.tree(readme=raw)), "readme", strict=False)

    def test_a_heading_inside_a_code_fence_does_not_count(self):
        fenced = "# fixture\n\n## What changed in 1.1.8\nold\n\n```\n## What changed in 1.1.9\n```\n"
        self.assertRefused(self.preflight(self.tree(readme=fenced)), "readme")

    def test_a_longer_version_number_does_not_count(self):
        longer = "# fixture\n\n## What changed in 1.1.8\nold\n\n## What changed in 1.1.90\nnot this one\n"
        self.assertRefused(self.preflight(self.tree(readme=longer)), "readme")

    def test_required_fast_declares_the_readme_section_test(self):
        """The gate's own declaration phase: with an unreadable order pin the script records every
        check it would run (name and argv) and then stops with exit 2 before running any. The
        README section test must be among them."""
        scratch = os.path.join(self.tmp, "gate-tmp")
        os.makedirs(scratch)
        env = dict(os.environ, TMPDIR=scratch, BROTHER_HEAVY_SLOT="off",
                   REQUIRED_FAST_ORDER_PIN=os.path.join(scratch, "no-such-pin.json"))
        proc = subprocess.run(["sh", os.path.join(ROOT, "scripts", "required_fast.sh")], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=300)
        pins = [d for d in os.listdir(scratch) if d.startswith("required-fast-pin")]
        self.assertTrue(pins, "required_fast.sh did not reach its declaration phase (exit %s): %s"
                        % (proc.returncode, (proc.stdout + proc.stderr)[-300:]))
        declared = []
        for name in os.listdir(os.path.join(scratch, pins[0])):
            if name.endswith(".argv"):
                with open(os.path.join(scratch, pins[0], name), encoding="utf-8") as fh:
                    declared.append(fh.read())
        self.assertTrue(declared, "required_fast.sh declared no checks")
        self.assertTrue(any("test_readme_changed_sections.py" in argv for argv in declared),
                        "no declared check runs scripts/test_readme_changed_sections.py")


# =====================================================================================
# RDM1.b  X01 (the macOS half, read by cut_preflight)
# =====================================================================================

class X01(Cut):
    """REQ-RDM1B-01 to 03. Mutations: M-RDM1B-ONE-HOST, M-RDM1B-BIN-OK."""

    def test_x01_refuses_without_both_hosts(self):
        """J2's record is stamped by the other host, so the macOS half of J2 is missing: refused.
        With both stamped macos the cut is clear. Kills M-RDM1B-ONE-HOST (one host is enough)."""
        self.pair("x01", {"j2": journey_record("J2", host="ubuntu")})

    def test_a_maintainer_bin_refuses(self):
        """The J1 walk's HOME held a maintainer bin (the walker lists its entries in claude_bin):
        refused, though every row is PASS. Kills M-RDM1B-BIN-OK (a maintainer bin is allowed).
        The fixture path names no real config directory, because the exported tree may not."""
        self.pair("x01", {"j1": journey_record("J1", claude_bin=["/home/m/maintainer-bin/or_ask.py"])})

    def test_a_missing_host_refuses(self):
        """No macOS record at all: refused or NO-DATA, never clear."""
        self.assertRefused(self.preflight(self.tree(j1=ABSENT, j2=ABSENT)), "x01", strict=False)

    def test_a_missing_journey_refuses(self):
        self.assertRefused(self.preflight(self.tree(j2=ABSENT)), "x01", strict=False)

    def test_a_record_for_another_commit_refuses(self):
        other = hashlib.sha256(b"another candidate").hexdigest()
        self.assertRefused(self.preflight(self.tree(
            j1=journey_record("J1", identity=other, candidate_identity=other))), "x01")

    def test_an_unknown_host_refuses(self):
        self.assertRefused(self.preflight(self.tree(j1=journey_record("J1", host="windows"))), "x01")

    def test_a_row_that_is_not_pass_refuses(self):
        """NO-DATA is never a pass: one J2 row reads NO-DATA."""
        rows = dict(JOURNEY_ROWS["J2"], **{"D.end-card-six-lines": "NO-DATA"})
        self.assertRefused(self.preflight(self.tree(j2=journey_record("J2", rows=rows))), "x01")

    def test_a_dirty_home_refuses(self):
        self.assertRefused(self.preflight(self.tree(j1=journey_record("J1", clean_home=False))), "x01")

    def test_an_unreadable_record_refuses(self):
        self.assertRefused(self.preflight(self.tree(j1=b"\xff\xfe{ not json")), "x01", strict=False)


# =====================================================================================
# RDM1.c  ReMeasure
# =====================================================================================

class ReMeasure(Cut):
    """REQ-RDM1C-01. Mutation: M-RDM1C-SELF-OK (JT0's author may re-measure)."""

    def test_the_cut_needs_a_hand_re_measure(self):
        """A re-measure by the seat that walked the journeys refuses; by another seat it is clear.
        Kills M-RDM1C-SELF-OK."""
        self.assertGood(self.preflight(self.tree()), "measure")
        self.assertRefused(self.preflight(self.tree(
            remeasure=remeasure_record(seat="seat-jt0"))), "measure")

    def test_a_missing_re_measure_refuses(self):
        self.assertRefused(self.preflight(self.tree(remeasure=ABSENT)), "measure", strict=False)

    def test_a_re_measure_of_another_candidate_refuses(self):
        other = hashlib.sha256(b"another candidate").hexdigest()
        self.assertRefused(self.preflight(self.tree(
            remeasure=remeasure_record(candidate_identity=other))), "measure")

    def test_a_re_measure_without_a_seat_refuses(self):
        for seat in ("", None, "   "):
            self.assertRefused(self.preflight(self.tree(remeasure=remeasure_record(seat=seat))), "measure")

    def test_an_unscored_cell_refuses(self):
        rec = remeasure_record()
        rec["cells"]["J1/D.six-verbs-only"]["score"] = None
        self.assertRefused(self.preflight(self.tree(remeasure=rec)), "measure")

    def test_a_cell_without_evidence_refuses(self):
        rec = remeasure_record()
        rec["cells"]["J2/D.end-card-six-lines"]["evidence"] = "  "
        self.assertRefused(self.preflight(self.tree(remeasure=rec)), "measure")

    def test_a_journey_cell_nobody_scored_refuses(self):
        rec = remeasure_record()
        del rec["cells"]["J1/A.first-screen-paths-exist"]
        self.assertRefused(self.preflight(self.tree(remeasure=rec)), "measure")

    def test_an_unreadable_re_measure_refuses(self):
        self.assertRefused(self.preflight(self.tree(remeasure=b"\xff\xfe{ not json")), "measure", strict=False)


def main(argv):
    names = [a for a in argv if a != "--selftest"]
    unittest.main(module=sys.modules[__name__], argv=[sys.argv[0]] + names, verbosity=1)


if __name__ == "__main__":
    main(sys.argv[1:])
