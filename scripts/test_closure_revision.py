#!/usr/bin/env python3
"""LOOP1.c: a unit closes on a done check that proved a named revision, and the closure leaves a structured `closed` event.

usage: python3 -B scripts/test_closure_revision.py
scripts/test_loop_delivery.py Delivered is the pack's frozen LOOP1.c harness and is bound to 1.1.9 (it reads the AUTH1
landing), so it cannot be satisfied here; the SPEC says its independent owner migrates it. These cases are the ones the
closer owes at its ENTRY POINT, close_unit.py run as a process over a real git repository (the shape scripts/
test_landing_facts.py uses): a control is tested where it lives, never only at a helper it calls.

ONE CONDITION PER CASE. Every case differs from the clean landing in exactly one fact, so only the guard it names can
refuse it. A host that cannot run the done check under the landing sandbox answers NO-DATA by name (a skip), never a pass."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "loop")]
import plan_store  # noqa: E402
from test_landing_facts import PLAN_REL, RepoFixture, eid  # noqa: E402


def subprocess_rm(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def events_of(unit, kind):
    return [e for e in unit.get("landing_events", []) if e["type"] == kind]


class Closed(RepoFixture, unittest.TestCase):

    def with_check(self, text):
        """The clean landing, then a commit that changes only what the done check prints."""
        d, landing, line = self.repo()
        with open(os.path.join(d, "check.py"), "w") as fh:
            fh.write(text)
        self.git(d, "commit", "-q", "-am", "the check prints something else")
        return d, landing, line

    def test_a_zero_exit_with_no_result_line_never_closes(self):
        """A shell exit is not a phase verdict: the check exits 0 and prints no result line, the one difference."""
        d, _, _ = self.with_check("print('all fine')\n")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("no result line", r.stdout)

    def with_chain(self, quiet_second):
        """The clean landing whose done check is two steps: check.py (prints PASS) and a second step that either prints a
        result line or prints nothing. The second step's output is the one difference between the chain cases."""
        import json
        d, _, _ = self.repo()
        with open(os.path.join(d, "second.py"), "w") as fh:
            fh.write("pass\n" if quiet_second else "print('PASS: the second step ran')\n")
        path = os.path.join(d, PLAN_REL)
        with open(path) as fh:
            plan = json.load(fh)
        plan["units"][0]["done_check"] = "python3 -B check.py && python3 -B second.py"
        with open(path, "w") as fh:
            json.dump(plan, fh, indent=1)
        self.git(d, "add", "-A")
        self.git(d, "commit", "-q", "-m", "a two step done check")
        return d

    def test_a_zero_exit_with_a_no_data_line_never_closes(self):
        """A check that exits 0 having printed NO-DATA measured nothing: it is not a pass, and the unit stays open."""
        d, _, _ = self.with_check("print('NO-DATA: nothing was measured')\n")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("NO-DATA: nothing was measured", r.stdout)

    def test_a_journey_whose_last_row_reads_no_data_closes_on_its_exit(self):
        """A walk exits 0 when its --expect rows PASS, whatever an unrelated trailing row reads: the exit decides, not the
        last row, so "NO-DATA  <row>" is a data row and the unit closes."""
        d, _, _ = self.with_check("print('PASS  A.first-row')\nprint('NO-DATA  B.hook-cost-row')\n")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual("DONE", unit["state"], r.stdout)

    def test_a_green_suite_whose_fixtures_print_no_data_closes(self):
        """A suite that exercises NO-DATA paths prints those lines before its own OK: its verdict is the OK, and it closes."""
        d, _, _ = self.with_check("print('NO-DATA: a fixture row, as the suite expects')\nprint('Ran 3 tests')\n"
                                  "print('OK')\n")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual("DONE", unit["state"], r.stdout)

    def test_a_last_step_that_prints_nothing_never_closes(self):
        """Each step of a chained done check carries its own verdict: an earlier step's PASS does not cover a later one."""
        d = self.with_chain(quiet_second=True)
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotEqual(unit["state"], "DONE")
        self.assertIn("step 2", r.stdout)

    def test_a_chain_whose_every_step_prints_a_verdict_closes(self):
        """The positive control for the case above: the same chain with a verdict line from each step closes."""
        d = self.with_chain(quiet_second=False)
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual("DONE", unit["state"], r.stdout)

    def test_the_closure_names_the_revision_it_proved(self):
        """The closed event's commit is the HEAD the check ran over, and its evidence_id hashes the line it appended."""
        d, landing, line = self.repo()
        head = self.git(d, "rev-parse", "HEAD")
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        closed = events_of(unit, "closed")
        self.assertEqual(1, len(closed), unit.get("landing_events"))
        tail = unit["evidence"][len(line):]
        self.assertIn(head[:12], tail, "the closure line names no revision: %r" % tail)
        self.assertEqual((head, eid(tail), "U1", "U1.a"), (closed[0]["commit"], closed[0]["evidence_id"],
                                                          closed[0]["unit"], closed[0]["sub"]))

    def test_the_landing_still_reads_landed_after_the_closure(self):
        """The closed event takes its own sequence: reusing the landing's would read as two events at one sequence."""
        d, _, _ = self.repo()
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        got = plan_store.landing_facts(unit["landing_events"], "U1", "U1.a")
        self.assertTrue(got["landed"], got)
        self.assertEqual("landed at sequence 1", got["reason"])

    def test_a_second_closer_run_closes_nothing_and_appends_nothing(self):
        """Resume after an interrupt: the closure happened once, so a second run adds no landing and no closure."""
        d, _, _ = self.repo()
        r, first = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((1, 1), (len(events_of(first, "landed")), len(events_of(first, "closed"))))
        r, second = self.close(d)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(first, second)

    def test_a_directory_git_cannot_name_a_head_for_is_no_data(self):
        """The revision a check proves must be readable: outside a repository head_revision answers None, never a guess."""
        import tempfile
        import close_unit
        here, d = os.getcwd(), tempfile.mkdtemp(prefix="loop-close-nogit-")
        self.addCleanup(os.rmdir, d)
        os.chdir(d)
        try:
            self.assertIsNone(close_unit.head_revision())
        finally:
            os.chdir(here)

    def test_a_check_that_passed_over_an_unnamed_revision_never_closes(self):
        """main()'s own refusal: the check passed, but git named no HEAD before it ran, so nothing says what it proved.
        A legacy unit and a stubbed check keep git out of the case; the unnamed revision is the one difference."""
        import contextlib
        import io
        import json
        import tempfile
        import close_unit
        d = tempfile.mkdtemp(prefix="loop-close-norev-")
        self.addCleanup(subprocess_rm, d)
        os.makedirs(os.path.join(d, "docs", "plan"))
        plan = os.path.join(d, PLAN_REL)
        with open(plan, "w") as fh:
            json.dump({"units": [{"id": "U1", "state": "PARTIAL", "sub_units": ["U1.a"], "evidence": "U1.a landed 2026-11-01.",
                                  "done_check": "python3 -B check.py"}]}, fh)
        here, was = os.getcwd(), (close_unit.PLAN, close_unit.head_revision, close_unit.run_done_check)
        os.chdir(d)
        close_unit.PLAN = PLAN_REL
        close_unit.head_revision = lambda: None
        close_unit.run_done_check = lambda steps, env: (0, "PASS: stubbed\n")
        out = io.StringIO()
        evidence_was = os.environ.get("BROTHER_EVIDENCE")
        os.environ["BROTHER_EVIDENCE"] = d   # STAB1.f: never the real evidence directory
        try:
            with contextlib.redirect_stdout(out):
                code = close_unit.main(["U1"])
        finally:
            os.chdir(here)
            if evidence_was is None:
                os.environ.pop("BROTHER_EVIDENCE", None)
            else:
                os.environ["BROTHER_EVIDENCE"] = evidence_was
            close_unit.PLAN, close_unit.head_revision, close_unit.run_done_check = was
        with open(plan) as fh:
            state = json.load(fh)["units"][0]["state"]
        self.assertEqual(1, code, out.getvalue())
        self.assertNotEqual("DONE", state)
        self.assertIn("git could not name HEAD", out.getvalue())

    def test_a_legacy_unit_gets_no_events_key(self):
        """P2b: a unit landed before LOOP1.d stays legacy after its closure, or its next close would read as structured."""
        d, _, _ = self.repo(legacy=True)
        r, unit = self.close(d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual("DONE", unit["state"])
        self.assertNotIn("landing_events", unit)


if __name__ == "__main__":
    unittest.main()
