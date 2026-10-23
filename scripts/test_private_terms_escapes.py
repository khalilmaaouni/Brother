#!/usr/bin/env python3
r"""PRIV1 (2026-10-18): a private term spelled with string escapes, or split across adjacent string
literals, is still a private term, and no gate may hold a term of its own.

THE DEFECT. A gate that counted private terms from string literals, each with its first letter
written as a backslash u escape, kept those terms in code, and the export's scan,
scripts/private_terms_scan.py, matched the text on disk, a backslash and four hex digits, so it
never saw a word the interpreter spells out in full.

TWO FIXES, each proven here at its entry point:
  1. the scan looks for every term in every reading of the text (private_terms_scan.text_views:
     escapes decoded, adjacent literals joined), through its real CLI over a real commit;
  2. grade_build.private_hits holds no term: it reads the one private list through the scan's own
     load_terms, in the checkout layout and in the deployed flat bin layout, and fails closed when
     the list or the scanner cannot be had.

Every term here is FAKE (zqxfake), written into a temporary list. Real terms never appear in this
repository, and nothing here prints one.

Run: python3 -B scripts/test_private_terms_escapes.py
"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOOP = os.path.join(HERE, "loop")
sys.path.insert(0, HERE)
import private_terms_scan as P  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

TERM = "zqxfake"
TOOL = os.path.join(HERE, "private_terms_scan.py")

#: Fixtures are written with "|" where a backslash goes and turned into one at import: a backslash and
#: four hex digits typed into a file can be decoded on the way in by an editor or a tool (it happened
#: while this file was written), and the test would then prove nothing.
B = chr(92)


def esc(text):
    return text.replace("|", B)


#: The old shape and its siblings: every one spells the fake term once the interpreter reads it.
ESCAPED = {
    "first letter as a four digit escape": esc('name = "|u007aqxfake"'),
    "first letter as a two digit escape": esc('name = "|x7aqxfake"'),
    "first letter as an eight digit escape": esc('name = "|U0000007aqxfake"'),
    "first letter by its character name": esc('name = "|N{LATIN SMALL LETTER Z}qxfake"'),
    "first letter as an octal escape": esc('name = "|172qxfake"'),
    "every letter escaped": esc('name = "|x7a|x71|x78|x66|x61|x6b|x65"'),
    "upper case, first letter escaped": esc('name = "|u005aQXFAKE"'),
    "split into adjacent literals": 'name = "zqx" "fake"',
    "split across lines in parentheses": 'name = ("zqx"\n        "fake")',
    "split with mixed quotes and a prefix": 'name = \'zq\' r"xfake"',
    "split with a plus sign": 'name = "zq" + "xfake"',
    "escaped and split": esc('name = "|u007aq" "xfake"'),
    "an escaped backslash spelling a second escape": esc('name = "|x5cu007aqxfake"'),
    "an HTML character reference": "<p>&#x7a;qxfake</p>",
}

PLAIN = ["we shipped zqxfake today", "ZQXFAKE in upper case", "see path_zqxfake_file"]

CLEAN = [
    "nothing private here",
    esc('"|u0041BC" is only ABC'),
    '"zqx", "fake"',
    "zqxfakes is a longer word",
    esc('name = "|u007aqxfakes"'),
]


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TheScanReadsEveryReadingOfTheText(unittest.TestCase):
    def test_the_old_shape_and_its_siblings_are_refused(self):
        for why, text in ESCAPED.items():
            self.assertEqual(P.scan_text(text, [TERM]), [TERM], why)

    def test_plain_text_still_matches(self):
        for text in PLAIN:
            self.assertEqual(P.scan_text(text, [TERM]), [TERM], text)

    def test_clean_text_passes(self):
        for text in CLEAN:
            self.assertEqual(P.scan_text(text, [TERM]), [], text)

    def test_joining_literals_never_hides_the_word_as_written(self):
        """Joining glues a literal to its neighbour, so it only ADDS readings: the text as written
        is always read too, or "x" "zqxfake" would read as xzqxfake and pass."""
        self.assertEqual(P.scan_text('print("x" "zqxfake")', [TERM]), [TERM])
        self.assertEqual(P.scan_text(esc('print("x" "|x7aqxfake")'), [TERM]), [TERM],
                         "the decoded reading must be kept beside the joined one")

    def test_decoding_never_hides_the_word_as_written(self):
        """A reference that decodes to a letter glues it to the term (&#65;zqxfake reads as
        Azqxfake), so the text as written must be read too."""
        self.assertEqual(P.scan_text("&#65;zqxfake", [TERM]), [TERM])


class TheScanCLIRefusesTheOldShapeInACommit(unittest.TestCase):
    """The export runs the scan as `private_terms_scan.py --terms <list> --range HEAD` over the
    candidate tree, so the entry point is the CLI over a real commit, not scan_text alone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="priv1-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.terms = os.path.join(self.tmp, "terms")
        write(self.terms, "# fake list\n%s\n" % TERM)
        self.repo = os.path.join(self.tmp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-q")

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"] + list(args),
                       cwd=self.repo, check=True, capture_output=True)

    def scan(self, content):
        write(os.path.join(self.repo, "grade.py"), content)
        self.git("add", "grade.py")
        self.git("commit", "-q", "-m", "fixture")
        return subprocess.run([sys.executable, "-B", TOOL, "--terms", self.terms, "--range", "HEAD"],
                              cwd=self.repo, capture_output=True, text=True)

    def test_an_escaped_term_in_a_commit_is_refused_and_never_printed(self):
        proc = self.scan(esc('def hits(text):\n    return text.find("|u007aqxfake")\n'))
        self.assertEqual(proc.returncode, P.EXIT_FOUND, proc.stdout + proc.stderr)
        self.assertNotIn(TERM, (proc.stdout + proc.stderr).lower())

    def test_a_clean_commit_passes(self):
        proc = self.scan('def hits(text):\n    return text.count("nothing")\n')
        self.assertEqual(proc.returncode, P.EXIT_CLEAN, proc.stdout + proc.stderr)


def _private_hits_by(cwd, path_dir, home, text, env_extra=None):
    """grade_build.private_hits run the way a loop tool runs it: a fresh interpreter with only the
    tool's own directory on sys.path, so nothing this test imported can stand in for the scanner."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BROTHER_") and k != "PYTHONPATH"}
    env.update(HOME=home, **(env_extra or {}))
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import grade_build as G; before = list(sys.path); "
            "n = G.private_hits(sys.argv[2]); print('PATH-KEPT' if sys.path == before else 'PATH-CHANGED'); print(n)")
    proc = subprocess.run([sys.executable, "-B", "-c", code, path_dir, text], cwd=cwd, env=env,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError("private_hits run failed: " + proc.stderr[-600:])
    out = proc.stdout.strip().splitlines()
    if out[-2] != "PATH-KEPT":
        raise AssertionError("private_hits left the scanner's folder on sys.path")
    return int(out[-1])


class TheBriefScreenHoldsNoTermOfItsOwn(unittest.TestCase):
    """grade_build.private_hits, the one function every loop gate calls before text leaves."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, LOOP)
        import grade_build
        cls.G = grade_build

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="priv1-home-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.names = os.path.join(self.home, ".brothersbe-private-names")
        write(self.names, "# fake list\n%s\n" % TERM)
        saved = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(os.environ.__setitem__, "HOME", saved)

    def test_the_old_shape_and_its_siblings_count(self):
        for why, text in ESCAPED.items():
            self.assertGreater(self.G.private_hits(text), 0, why)

    def test_plain_text_counts_and_clean_text_does_not(self):
        for text in PLAIN + ["&#65;zqxfake", esc('print("x" "|x7aqxfake")')]:
            self.assertGreater(self.G.private_hits(text), 0, text)
        for text in CLEAN:
            self.assertEqual(self.G.private_hits(text), 0, text)

    def test_a_term_stored_in_capitals_counts_anywhere_in_capitals(self):
        """The client codes matched anywhere when they were literals in grade_build; moving them into the
        list must not weaken that: a capitals term inside a longer capitals word still counts, while the
        same letters inside an ordinary lower case word do not."""
        write(self.names, "%s\nQXZK\n" % TERM)
        for text in ("the QXZKs report", "QXZKJAPAN", "a qxzk here", esc('name = "|x51XZK" "s"')):
            self.assertGreater(self.G.private_hits(text), 0, text)
        self.assertEqual(self.G.private_hits("an aqxzkb sound"), 0)

    def test_a_missing_list_counts_as_a_hit(self):
        os.remove(self.names)
        self.assertEqual(self.G.private_hits("nothing private here"), 1)

    def test_an_empty_list_counts_as_a_hit(self):
        """A list with no term screens nothing, which must never read as a clean text."""
        write(self.names, "# only a comment\n\n")
        self.assertEqual(self.G.private_hits("nothing private here"), 1)

    def test_a_text_that_is_not_a_string_counts_as_a_hit(self):
        for value in (None, 7, TERM.encode()):
            self.assertEqual(self.G.private_hits(value), 1, repr(value))

    def test_private_hits_carries_no_term_literal(self):
        """The terms left the code: no string constant in private_hits is a bare word, and none of
        its calls counts a literal."""
        with open(os.path.join(LOOP, "grade_build.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "private_hits"]
        self.assertEqual(len(fn), 1)
        # COUNTS ONLY, never the list: a failing assertEqual on the list prints every word it found, and
        # anything it finds here would be a real term.
        words = sum(1 for c in ast.walk(fn[0]) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and len(c.value) >= 4 and c.value.isalpha())
        self.assertEqual(words, 0, "bare word literals in private_hits")
        counted = sum(1 for c in ast.walk(fn[0]) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "count"
                      and c.args and isinstance(c.args[0], ast.Constant))
        self.assertEqual(counted, 0, "calls in private_hits that count a literal")


class TheScreenFindsTheScannerWhereverTheLoopRuns(unittest.TestCase):
    """The loop runs grade_build from scripts/loop (scripts/ beside it) and from the deployed flat
    bin (~/.claude/bin, whose parent holds no scanner: the code root does)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="priv1-layout-")
        cls.home = os.path.join(cls.tmp, "home")
        os.mkdir(cls.home)
        write(os.path.join(cls.home, ".brothersbe-private-names"), "%s\n" % TERM)
        cls.bin = os.path.join(cls.tmp, "home", ".claude", "bin")
        os.makedirs(cls.bin)
        for name in ("grade_build.py", "model_router.py", "plan_path.py", "loop_switches.py"):
            shutil.copy(os.path.join(LOOP, name), cls.bin)
        cls.elsewhere = os.path.join(cls.tmp, "not-a-checkout")
        os.mkdir(cls.elsewhere)
        # a landing tree: the working directory is ANOTHER repository, whose code root holds no scanner
        cls.foreign = os.path.join(cls.tmp, "foreign-repo")
        os.mkdir(cls.foreign)
        subprocess.run(["git", "init", "-q"], cwd=cls.foreign, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_the_checkout_layout_finds_the_scanner_beside_it(self):
        """Run from inside another repository, as a landing tool is: the code root would name that
        tree, which holds no scanner, so only the scanner beside grade_build can answer."""
        escaped = ESCAPED["first letter as a four digit escape"]
        self.assertGreater(_private_hits_by(self.foreign, LOOP, self.home, escaped), 0)
        self.assertEqual(_private_hits_by(self.foreign, LOOP, self.home, "nothing private here"), 0)

    def test_a_flat_bin_reaches_the_scanner_through_the_code_root(self):
        escaped = ESCAPED["first letter as a four digit escape"]
        self.assertGreater(_private_hits_by(REPO, self.bin, self.home, escaped), 0)
        self.assertEqual(_private_hits_by(REPO, self.bin, self.home, "nothing private here"), 0)

    def test_a_flat_bin_with_no_code_root_fails_closed(self):
        self.assertEqual(_private_hits_by(self.elsewhere, self.bin, self.home, "nothing private here"), 1)


if __name__ == "__main__":
    unittest.main()
