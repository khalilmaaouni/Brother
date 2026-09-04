"""What the private terms scan must keep true.

Every term used here is FAKE. Real ones live outside every repository, at
~/.brothersbe-private-names, and the reason is the thing that prompted this
control: a scanner committed together with its fixtures publishes exactly what
it exists to stop, and that has already happened once in this estate's sibling
repository.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import private_terms_scan as P  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "private_terms_scan.py")


class Proc(object):
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class TheLengthOfTheTermDecidesItsStrictness(unittest.TestCase):
    """CORRECTED 2026-09-03 (readiness row E34): strictness used to be decided
    by the term's STORED SPELLING (`term.isupper()`), which took a
    case-sensitive path for a term this estate stores in upper case and
    missed a lowercase occurrence of it. Now LENGTH alone decides, mirroring
    scripts/cleanse.sh's own `${#needle} -le 5` branch: a term of five
    characters or fewer, or longer, matches as a whole word, case
    insensitively."""

    def test_a_short_term_matches_whole_words_only(self):
        self.assertEqual(P.scan_text("we shipped to ACME", ["ACME"]), ["ACME"])
        self.assertEqual(P.scan_text("an acmestic mood", ["ACME"]), [])

    def test_a_short_UPPERCASE_stored_term_now_matches_lowercase_too(self):
        """The exact E34 defect: a short term stored upper case used to take
        the case-sensitive branch and miss this occurrence."""
        self.assertEqual(P.scan_text("we shipped to acme", ["ACME"]), ["ACME"])

    def test_a_mixed_case_term_matches_any_casing(self):
        for text in ("the Lakeside bottler", "LAKESIDE", "lakeside"):
            self.assertEqual(P.scan_text(text, ["Lakeside"]), ["Lakeside"], text)

    def test_a_mixed_case_term_does_not_fire_on_its_parts(self):
        self.assertEqual(P.scan_text("a lake, then a side", ["Lakeside"]), [])

    def test_a_long_term_glued_inside_a_longer_word_is_not_reported(self):
        """DISCREPANCY NOTED AND RESOLVED: this mirrors cleanse.sh's ACTUAL
        `${#needle} -gt 5` arm, which since commit ea1bb937 (2026-08-30,
        "the gate stops matching long terms inside English words") also
        runs whole-word matching, not the plain substring match an older,
        unedited comment above that file's length check still describes.
        Plain substring matching for long terms was cleanse.sh's OLD
        behavior and was corrected away because it fired on ordinary
        English prose. This test locks in the current, tested behavior."""
        self.assertEqual(P.scan_text("xxLakesidexx here", ["Lakeside"]), [])

    def test_an_underscore_is_a_boundary_so_path_term_file_is_a_hit(self):
        """E37, 2026-09-03. `\\b` treats the underscore as a word character,
        so path_<term>_file used to pass this scanner while the assurance
        product's history test (isalnum bounds) refused it. Driven both
        ways: underscore-adjacent is a hit for a short and a long term; a
        term glued inside a longer run of letters is still not one."""
        self.assertEqual(P.scan_text("see path_acme_file", ["ACME"]), ["ACME"])
        self.assertEqual(P.scan_text("in lakeside_export", ["Lakeside"]),
                         ["Lakeside"])
        self.assertEqual(P.scan_text("an xxacmexx token", ["ACME"]), [])
        self.assertEqual(P.scan_text("xxLakesidexx here", ["Lakeside"]), [])

    def test_the_default_list_is_the_one_the_law_names(self):
        """E37: the exporter, this scanner and bm_private_scan.py must read
        ONE file, the one the estate's law names, never a second copy."""
        self.assertEqual(P.DEFAULT_TERMS_FILE,
                         os.path.expanduser("~/.brothersbe-private-names"))

    def test_several_terms_are_all_reported(self):
        got = P.scan_text("ACME met Lakeside", ["ACME", "Lakeside", "Unused"])
        self.assertEqual(sorted(got), ["ACME", "Lakeside"])


class AMissingListIsNoDataAndNeverAnEmptyOne(unittest.TestCase):
    def test_a_missing_file_returns_None_not_an_empty_list(self):
        """An empty list makes every scan pass, which is how a control silently
        stops working while still reporting green."""
        self.assertIsNone(P.load_terms("/no/such/terms/file"))

    def test_the_CLI_exits_NO_DATA_rather_than_clean(self):
        self.assertEqual(P.main(["--terms", "/no/such/terms/file"]),
                         P.EXIT_NO_DATA)

    def test_an_empty_file_is_also_NO_DATA(self):
        import tempfile
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            self.assertEqual(P.main(["--terms", path]), P.EXIT_NO_DATA)
        finally:
            os.unlink(path)

    def test_comments_and_blank_lines_are_skipped(self):
        import tempfile
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("# a comment\n\nACME\n\n# another\nLakeside\n")
        try:
            self.assertEqual(P.load_terms(path), ["ACME", "Lakeside"])
        finally:
            os.unlink(path)


class ItScansWhatAPushWouldSend(unittest.TestCase):
    """The law binds the HISTORY, not the working tree: a repository that ever
    held the content still holds it in its objects."""

    def test_a_term_DELETED_in_the_last_commit_is_still_found(self):
        """This is the whole reason it reads the patch and not the tree."""
        patch = "commit abc\n--- a/f.py\n+++ b/f.py\n-secret = 'ACME'\n"
        found, err = P.scan_range("range", ["ACME"],
                                  runner=lambda cmd: Proc(stdout=patch))
        self.assertEqual(found, ["ACME"])

    def test_an_unreadable_range_is_NO_DATA_not_clean(self):
        found, err = P.scan_range("bad", ["ACME"],
                                  runner=lambda cmd: Proc(returncode=128,
                                                          stderr="no such ref"))
        self.assertIsNone(found)
        self.assertIn("no such ref", err)

    def test_a_branch_the_remote_has_never_seen_scans_the_WHOLE_branch(self):
        """The first push is the one that matters most, and there is no
        remote-side base to diff against."""
        def runner(cmd):
            if "--abbrev-ref" in cmd:
                return Proc(stdout="feature\n")
            return Proc(returncode=1, stdout="")     # remote does not have it
        self.assertEqual(P.outgoing_range("origin", runner=runner), "feature")

    def test_a_branch_the_remote_HAS_scans_only_what_is_new(self):
        def runner(cmd):
            if "--abbrev-ref" in cmd:
                return Proc(stdout="feature\n")
            return Proc(stdout="deadbeef\n")
        self.assertEqual(P.outgoing_range("origin", runner=runner),
                         "origin/feature..feature")


class ItNeverPrintsTheTermItFound(unittest.TestCase):
    def test_the_refusal_message_carries_a_count_and_not_the_words(self):
        """Printing them would put them in a terminal, a CI log and a
        transcript, which is the thing being prevented."""
        import io
        import contextlib
        import tempfile
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write("ACME\n")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                code = P.main(["--terms", path, "--range", "HEAD~0"])
        finally:
            os.unlink(path)
        self.assertNotIn("ACME", buf.getvalue())
        self.assertIn(code, (P.EXIT_CLEAN, P.EXIT_FOUND, P.EXIT_NO_DATA))


class TheSelftestIsRunnableByHand(unittest.TestCase):
    def test_selftest_exits_zero(self):
        self.assertEqual(P._selftest(), 0)


#: Invented fixtures, mirroring scripts/test_cleanse.py's SHORT_TERM (four
#: characters, takes the short/whole-word branch) and LONG_TERM (over five,
#: takes the long branch). Never the real list: see the module docstring.
SHORT_TERM = "QZXW"
LONG_TERM = "LONGVENDOR"


class ItRunsAsASubprocessOverAScratchGitRepo(unittest.TestCase):
    """The acceptance-level tests, mirroring scripts/test_cleanse.py's own
    helpers (a temporary terms file, a temporary tree, the tool run as a
    subprocess). Extended with an actual scratch git repository, because
    unlike cleanse.sh (which scans a working tree) this tool's whole job is
    reading `git log -p` over a commit range: there is nothing to scan
    without real commits behind it."""

    def setUp(self):
        fd, self.terms_path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(SHORT_TERM + "\n" + LONG_TERM + "\n")
        self.roots = []

    def tearDown(self):
        os.remove(self.terms_path)
        for root in self.roots:
            shutil.rmtree(root, ignore_errors=True)

    def repo_with_note(self, note_text):
        """A scratch git repo: one base commit, then one commit whose diff
        introduces note_text. --range HEAD~1..HEAD below scans only that
        second commit's patch, the same object `git log -p` would show a
        real push sending."""
        root = tempfile.mkdtemp()
        self.roots.append(root)
        run = lambda cmd: subprocess.run(cmd, cwd=root, check=True,
                                          capture_output=True, text=True)
        run(["git", "init", "-q"])
        run(["git", "config", "user.email", "test@example.invalid"])
        run(["git", "config", "user.name", "Test"])
        note = os.path.join(root, "note.txt")
        with open(note, "w") as fh:
            fh.write("start\n")
        run(["git", "add", "note.txt"])
        run(["git", "commit", "-q", "-m", "base"])
        with open(note, "w") as fh:
            fh.write(note_text)
        run(["git", "add", "note.txt"])
        run(["git", "commit", "-q", "-m", "note"])
        return root

    def run_scan(self, root):
        proc = subprocess.run(
            [sys.executable, TOOL, "--terms", self.terms_path,
             "--range", "HEAD~1..HEAD"],
            cwd=root, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_a_lowercase_spelling_of_the_short_term_is_refused(self):
        root = self.repo_with_note(
            "the record mentions %s in passing\n" % SHORT_TERM.lower())
        code, out, err = self.run_scan(root)
        self.assertEqual(code, P.EXIT_FOUND, msg=out + err)

    def test_b_ordinary_word_containing_the_short_terms_letters_is_allowed(self):
        word = "a" + SHORT_TERM.lower() + "b"
        root = self.repo_with_note(
            "an ordinary %s appears in this note\n" % word)
        code, out, err = self.run_scan(root)
        self.assertEqual(code, P.EXIT_CLEAN, msg=out + err)

    def test_b2_underscore_adjacent_short_term_is_refused_end_to_end(self):
        # E37 through the real CLI over a real commit, not only scan_text.
        root = self.repo_with_note(
            "see path_%s_file for the export\n" % SHORT_TERM.lower())
        code, out, err = self.run_scan(root)
        self.assertEqual(code, P.EXIT_FOUND, msg=out + err)

    def test_c_the_long_term_is_refused_case_insensitively(self):
        # A differently-cased WHOLE-WORD occurrence, not one glued inside a
        # longer word: see ItRunsAsASubprocessOverAScratchGitRepo's docstring
        # and TheLengthOfTheTermDecidesItsStrictness's own glued-term case
        # above for why the long branch does not do plain substring matching.
        root = self.repo_with_note(
            "the contract cites %s as the party\n" % LONG_TERM.lower())
        code, out, err = self.run_scan(root)
        self.assertEqual(code, P.EXIT_FOUND, msg=out + err)

    def test_d_the_tool_never_prints_the_term_it_found(self):
        root = self.repo_with_note(
            "the record mentions %s in passing\n" % SHORT_TERM.lower())
        code, out, err = self.run_scan(root)
        combined = out + err
        self.assertEqual(code, P.EXIT_FOUND, msg=combined)
        self.assertNotIn(SHORT_TERM, combined)
        self.assertNotIn(SHORT_TERM.lower(), combined)
        self.assertNotIn(LONG_TERM, combined)
        self.assertNotIn(LONG_TERM.lower(), combined)
        self.assertIn("term(s)", combined,
                       msg="the count line must still be present")


if __name__ == "__main__":
    unittest.main()
