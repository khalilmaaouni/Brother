"""N1: discussion before planning stops being something you have to know to ask for.

The complaint, in the reviewer's own terms: they wanted a discussion before
anyone planned the work, did not get one, and nothing flagged or logged the
skip. check_clarify in tools/sbe_design.py is the answer: 09-clarify.md
records what is still undecided while it is still cheap to decide, and
check_clarify refuses a verdict while any row is open.

This file drives the seven verdict paths the introducing commit described by
hand (absent NO-DATA, template NO-DATA, open row FAIL, answer without a date
FAIL, malformed row FAIL, all answered PASS, empty table NO-DATA) as
repeatable assertions, because a claim made only in a commit message is a
claim nobody re-checks the next time the parser changes.

The empty-table path used to PASS ("a design with nothing open is a real
state"), on purpose, by design. `evals/test_no_data_class.py`'s honesty
meta-test (row E82, hub battery, 2026-09-04) caught this as exactly the
class-wide defect the project exists to prevent: a zero-row table declares
nothing, and `sbe_checks.Check`'s own constructor refuses any check that
declares `empty_expect="PASS"`, so no check may special-case its way past
that rule either. check_clarify now reads NO-DATA on zero rows, the same
answer check_behaviour already gives for its own empty table.
"""
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbe_design as D  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

NAME = D.ARTIFACT_FILES["09"]


def _dossier(text=None):
    d = tempfile.mkdtemp()
    if text is not None:
        with open(os.path.join(d, NAME), "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


_HEADER = ("# 09. Open questions\n\n"
           "| ID | Question | Asked by | Asked of | Answer | Answered |\n"
           "|---|---|---|---|---|---|\n")


class ClarifyVerdicts(unittest.TestCase):
    def setUp(self):
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _root(self, text=None):
        d = _dossier(text)
        self._dirs.append(d)
        return d

    def test_no_file_at_all_is_NO_DATA(self):
        verdict, note = D.check_clarify(self._root())
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("not a pass", note)

    def test_the_shipped_template_marker_is_NO_DATA(self):
        text = "<!-- %s -->\n%s" % (D.UNFILLED_MARKER, _HEADER)
        verdict, note = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn(NAME, note)

    def test_an_open_row_FAILs(self):
        text = _HEADER + "| Q1 | Which address wins? | analyst | owner | | |\n"
        verdict, note = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("still open", note)

    def test_an_answer_with_no_date_FAILs(self):
        text = _HEADER + "| Q1 | Which address wins? | analyst | owner | The newest one. | |\n"
        verdict, _ = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "FAIL")

    def test_a_date_with_no_answer_FAILs(self):
        text = _HEADER + "| Q1 | Which address wins? | analyst | owner | | 2026-08-29 |\n"
        verdict, _ = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "FAIL")

    def test_a_malformed_row_FAILs_and_is_named(self):
        text = _HEADER + "| Q1 | too few cells |\n"
        verdict, note = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "FAIL")
        self.assertIn("could not be read", note)

    def test_every_row_answered_and_dated_PASSes(self):
        text = _HEADER + ("| Q1 | Which address wins? | analyst | owner | The newest one. "
                           "| 2026-08-29 |\n")
        verdict, note = D.check_clarify(self._root(text))
        self.assertEqual(verdict, "PASS")
        self.assertIn("1 question", note)

    def test_an_empty_table_is_NO_DATA(self):
        """Same reading as check_behaviour's own empty table: a zero-row
        09-clarify.md declares nothing, and empty_expect can never be PASS
        for a check registered here (sbe_checks.Check's own constructor
        refuses it), so an empty table cannot be a special case."""
        verdict, note = D.check_clarify(self._root(_HEADER))
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("nothing here states", note)

    def test_an_unreadable_file_FAILs_not_NO_DATA(self):
        """A broken claim is not an absent one."""
        d = self._root()
        path = os.path.join(d, NAME)
        os.mkdir(path)  # a directory wearing the artifact's name
        verdict, note = D.check_clarify(d)
        self.assertEqual(verdict, "FAIL")
        self.assertIn("broken claim", note)


class ClarifyIsRegisteredAsAGateCheck(unittest.TestCase):
    def test_the_clarify_check_is_in_the_registry(self):
        self.assertIn("clarify", D.CHECKS)
        self.assertIs(D.CHECKS["clarify"].fn, D.check_clarify)

    def test_the_worked_example_passes(self):
        """The honesty meta-test enforces this generically; pinned here too so
        a break shows up in the suite that names this check."""
        d = self._root_from_fixture()
        verdict, _ = D.check_clarify(d)
        self.assertEqual(verdict, "PASS")
        shutil.rmtree(d, ignore_errors=True)

    def _root_from_fixture(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, NAME), "w", encoding="utf-8") as fh:
            fh.write(D._FX_CLARIFY)
        return d


if __name__ == "__main__":
    unittest.main()
