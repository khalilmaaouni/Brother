#!/usr/bin/env python3
"""RDM1.b: the Linux half of X01, as cut_driver.ubuntu_x01_gate reads it.

The cut phase publishes only when the newest finished virgin-install run that walked THIS candidate printed
J1 and J2 sections whose identity line names the candidate's runtime manifest, whose exit is 0 and whose rows
are all PASS. Anything unreadable, missing or unfinished is NO-DATA, a walked candidate that did not pass is
FAIL, and neither is ever PASS.

The gate is driven through a scripted gh handler (no network, no real gh). Each case spoils ONE thing, so
each guard of the gate has a case only it can fail.

    python3 -B scripts/test_cut_driver_ubuntu_gate.py
Python 3.9 compatible, standard library only.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cut_driver as D  # noqa: E402

MANIFEST = b'{"fixture": "the candidate"}\n'
IDENT = hashlib.sha256(MANIFEST).hexdigest()
OTHER = hashlib.sha256(b"another candidate").hexdigest()
#: gh prefixes every log line with job, step and a timestamp.
STAMP = "-".join(["2026", "10", "29"]) + "T" + ":".join(["10", "00", "00"]) + ".1Z"


def section(journey, verdicts=("PASS", "PASS"), candidate=IDENT, installed=IDENT, code=0):
    lines = ["=== %s ===" % journey]
    lines += ["%-8s %-34s %-28s %s" % (v, "A.row%d" % i, "atom", "said") for i, v in enumerate(verdicts)]
    lines += ["identity candidate=%s installed=%s" % (candidate, installed), "%s exit %d" % (journey, code)]
    return lines


def log(*sections, **extra):
    lines = [ln for sec in sections for ln in sec] + list(extra.get("after", []))
    return "".join("prove\tJourney\t%s %s\n" % (STAMP, ln) for ln in lines)


GOOD = log(section("J1"), section("J2", ("PASS",)))


def run(run_id, status="completed", created="a"):
    return {"databaseId": run_id, "status": status, "createdAt": created}


class Gh(object):
    """A scripted gh: `runs` is the list answer, `logs` maps a run id to its log (or (rc, text))."""

    def __init__(self, runs, logs, list_rc=0, list_text=None):
        self.runs, self.logs, self.list_rc, self.list_text = runs, logs, list_rc, list_text
        self.calls = []

    def __call__(self, cmd, cwd, stream=False):
        self.calls.append(list(cmd))
        if cmd[1:3] == ["run", "list"]:
            return self.list_rc, (json.dumps(self.runs) if self.list_text is None else self.list_text)
        if cmd[1:3] == ["run", "view"]:
            got = self.logs.get(int(cmd[3]), (1, "no such run"))
            return got if isinstance(got, tuple) else (0, got)
        return 127, "unhandled %s" % " ".join(cmd)


class UbuntuGate(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp(prefix="ubuntu-gate-")
        self.addCleanup(shutil.rmtree, self.wt, True)
        os.makedirs(os.path.join(self.wt, "bundle", "runtime"))
        with open(os.path.join(self.wt, "bundle", "runtime", "RUNTIME-" + "MANIFEST.json"), "wb") as fh:
            fh.write(MANIFEST)

    def gate(self, text, runs=None, **kw):
        gh = Gh([run(7)] if runs is None else runs, {7: text}, **kw)
        verdict, detail = D.ubuntu_x01_gate(self.wt, gh)
        self.gh = gh
        return verdict, detail

    def test_both_journeys_pass_on_this_candidate(self):
        verdict, _ = self.gate(GOOD)
        self.assertEqual(verdict, "PASS")
        self.assertEqual([c for c in self.gh.calls if "workflow" in c], [], "the gate dispatches nothing")

    def test_a_row_that_is_not_pass_is_fail(self):
        self.assertEqual(self.gate(log(section("J1", ("PASS", "NO-DATA")), section("J2")))[0], "FAIL")
        self.assertEqual(self.gate(log(section("J1"), section("J2", ("FAIL",))))[0], "FAIL")

    def test_a_journey_with_no_rows_is_fail(self):
        self.assertEqual(self.gate(log(section("J1", ()), section("J2")))[0], "FAIL")

    def test_a_missing_journey_is_fail(self):
        self.assertEqual(self.gate(log(section("J1")))[0], "FAIL")

    def test_a_nonzero_journey_exit_is_fail(self):
        self.assertEqual(self.gate(log(section("J1"), section("J2", code=2)))[0], "FAIL")

    def test_a_journey_that_walked_another_tree_is_fail(self):
        for bad in (section("J2", installed=OTHER), section("J2", candidate=OTHER)):
            verdict, detail = self.gate(log(section("J1"), bad))
            self.assertEqual(verdict, "FAIL", detail)

    def test_a_run_that_walked_another_candidate_is_no_data(self):
        verdict, detail = self.gate(log(section("J1", candidate=OTHER, installed=OTHER),
                                        section("J2", candidate=OTHER, installed=OTHER)))
        self.assertEqual(verdict, "NO-DATA", detail)

    def test_an_older_run_of_another_candidate_is_skipped_for_the_matching_one(self):
        gh = Gh([run(8, created="b"), run(7, created="a")],
                {8: log(section("J1", candidate=OTHER, installed=OTHER), section("J2", candidate=OTHER, installed=OTHER)),
                 7: GOOD})
        self.assertEqual(D.ubuntu_x01_gate(self.wt, gh)[0], "PASS")

    def test_the_newest_run_that_walked_the_candidate_decides(self):
        bad = log(section("J1"), section("J2", ("FAIL",)))
        gh = Gh([run(7, created="a"), run(8, created="b")], {7: GOOD, 8: bad})
        self.assertEqual(D.ubuntu_x01_gate(self.wt, gh)[0], "FAIL")
        gh = Gh([run(7, created="b"), run(8, created="a")], {7: GOOD, 8: bad})
        self.assertEqual(D.ubuntu_x01_gate(self.wt, gh)[0], "PASS")

    def test_a_run_still_in_progress_is_never_read(self):
        verdict, _ = self.gate(GOOD, runs=[run(7, status="in_progress")])
        self.assertEqual(verdict, "NO-DATA")
        self.assertEqual([c for c in self.gh.calls if c[1:3] == ["run", "view"]], [], "an unfinished run was read")

    def test_no_runs_is_no_data(self):
        self.assertEqual(self.gate(GOOD, runs=[])[0], "NO-DATA")

    def test_a_run_list_that_cannot_run_or_parse_is_no_data(self):
        self.assertEqual(self.gate(GOOD, list_rc=127, list_text="no gh")[0], "NO-DATA")
        self.assertEqual(self.gate(GOOD, list_text="not json")[0], "NO-DATA")
        self.assertEqual(self.gate(GOOD, list_text='{"a": 1}')[0], "NO-DATA")

    def test_a_log_that_cannot_be_read_is_no_data(self):
        gh = Gh([run(7)], {7: (1, "gone")})
        self.assertEqual(D.ubuntu_x01_gate(self.wt, gh)[0], "NO-DATA")

    def test_a_candidate_with_no_manifest_is_no_data(self):
        verdict, _ = D.ubuntu_x01_gate(tempfile.mkdtemp(prefix="no-manifest-"), Gh([run(7)], {7: GOOD}))
        self.assertEqual(verdict, "NO-DATA")

    def test_a_row_line_after_the_journey_exit_is_not_that_journeys_row(self):
        text = log(section("J1"), section("J2"), after=["NO-DATA a later step line"])
        self.assertEqual(self.gate(text)[0], "PASS")


if __name__ == "__main__":
    unittest.main()
