"""The edges of the row scoped unknown (STAB1.d): three properties the implementer's mutation sweep left untested.

CONTRACT (STABILITY-120.json): owned() answers None (unknown) only when a row whose environment could not be read
(run_dir None) matches the pattern being asked; loop_procs.main() turns that None into NO-DATA, exit 2, never into an
empty "nothing owned" list. Each case kills one survivor:
  (a) the unknown check ignored the `only` filter: a stop limited by STOP_LOOP_ONLY to text the unknown row does not
      carry still read NO-DATA instead of listing what it was asked about;
  (b) main() treated None as empty: the stop printed nothing and exited 0 over a row it could not judge;
  (c) the unknown check skipped ok(): the caller's own row (--self) made the answer unknown.
Driven at loop_procs.main() with the argv stop_loop.sh's procs() passes (`<kind> --only "$ONLY" --self "$$"`), over the
frozen harness's faked ps (scripts/test_procs_row_unknown.py: subprocess.run patched, HOME the placeholder /Users/you,
no BROTHER_* variable): nothing real is read or spawned.
Run from the repository root: python3 -B scripts/test_procs_row_unknown_edges.py
"""
import contextlib, io, os, sys, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "loop"))
import loop_procs as LP  # noqa: E402
import test_procs_row_unknown as F  # noqa: E402  (the frozen harness's fixtures, reused, never its test class)

TABLE = ["1 0 1 /sbin/launchd", "4242 1 4242 " + F.RUNNER, "5151 1 5151 " + F.STRANGER_DIAG]   # 5151: unreadable env


class RowUnknownEdges(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, F.env(), clear=True)
        p.start(); self.addCleanup(p.stop)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(LP.subprocess, "run", side_effect=F.fake_ps(TABLE, unreadable={5151})), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = LP.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_a_an_unknown_row_the_only_filter_excludes_leaves_the_answer_known(self):
        rc, out, err = self.main("runners", "--only", "U1.a", "--self", "1")
        self.assertEqual((rc, out), (0, "4242 %s\n" % F.RUNNER),
                         "an unknown row STOP_LOOP_ONLY excludes changed the answer: " + err)

    def test_b_main_reads_an_unknown_answer_as_no_data_never_as_nothing_owned(self):
        rc, out, err = self.main("runners", "--only", "", "--self", "1")
        self.assertEqual((rc, out), (2, ""), "main() listed or passed over a row it could not judge: " + out + err)
        self.assertIn("NO-DATA", err)

    def test_c_the_callers_own_unknown_row_leaves_the_answer_known(self):
        rc, out, err = self.main("runners", "--only", "", "--self", "5151")
        self.assertEqual((rc, out), (0, "4242 %s\n" % F.RUNNER),
                         "the caller's own row (--self) made the answer unknown: " + err)


if __name__ == "__main__":
    unittest.main()
