import io
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTATE = os.path.join(ROOT, "docs", "book", "estate")


class TestWorkedEstate(unittest.TestCase):
    """The book's example project is real: these are the exact commands the
    chapters paste, so a broken estate is a broken book."""

    def _run(self, *argv):
        out = subprocess.run([sys.executable] + list(argv), capture_output=True,
                             text=True, cwd=ESTATE, timeout=60)
        return out.returncode, out.stdout, out.stderr

    def test_the_pipeline_runs_and_names_its_output(self):
        code, stdout, stderr = self._run("pipeline.py", "--date", "2026-07-01")
        self.assertEqual(code, 0, stderr)
        self.assertIn("wrote 3 rows to daily_totals", stdout)

    def test_the_estate_suite_is_green(self):
        code, stdout, stderr = self._run("test_estate.py")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertIn("Ran 4 tests", stdout + stderr)


class TestBookBuild(unittest.TestCase):
    def test_the_build_produces_one_offline_html_with_every_chapter(self):
        out = subprocess.run([sys.executable,
                              os.path.join(ROOT, "docs", "book", "build_book.py")],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        html_path = os.path.join(ROOT, "docs", "book", "BrotherSBE-for-Dummies.html")
        self.assertTrue(os.path.exists(html_path))
        html = io.open(html_path, encoding="utf-8").read()
        chapters = sorted(n for n in os.listdir(os.path.join(ROOT, "docs", "book"))
                          if n[:2].isdigit() and n.endswith(".md"))
        self.assertGreaterEqual(len(chapters), 12)
        for name in chapters:
            title = io.open(os.path.join(ROOT, "docs", "book", name),
                            encoding="utf-8").readline().lstrip("# ").strip()
            self.assertIn(title, html, "chapter %s missing from the build" % name)
        self.assertIn("mermaid", html)
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html.split("</nav>")[0])


class TestInlineCodeSpans(unittest.TestCase):
    """A book about precision may not print literal backticks at its reader.

    The converter's first version handled bold and links but not code spans,
    so 191 spans across the chapters would have rendered as raw backticks.
    This fixture is the reason that cannot come back.
    """

    def test_code_spans_become_code_elements_and_leave_no_backticks(self):
        import re
        out = subprocess.run([sys.executable,
                              os.path.join(ROOT, "docs", "book", "build_book.py")],
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr)
        html = io.open(os.path.join(ROOT, "docs", "book",
                                    "BrotherSBE-for-Dummies.html"),
                       encoding="utf-8").read()
        body = html.split("</nav>", 1)[-1]
        body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
        body = re.sub(r"<pre.*?</pre>", "", body, flags=re.S)
        self.assertGreater(body.count("<code>"), 100,
                           "the chapters carry many code spans; none rendered")
        self.assertEqual(body.count("`"), 0,
                         "a literal backtick survived into rendered prose")


class TestDeclaredVolatileLine(unittest.TestCase):
    """The replay harness masks exactly one substring: the live merge-base
    diff line the status and impact tools print, which moves with every
    commit. These fixtures pin the mask to that substring and nothing else,
    calibrated both ways: a volatile-only difference must pass, and any
    other difference must still fail. Without the second half, the mask
    could silently widen and eat real drift."""

    def _stable(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "replay_book", os.path.join(ROOT, "evals", "replay_book.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.stable

    def test_a_volatile_only_difference_compares_equal(self):
        stable = self._stable()
        book = "clean. git diff 47422a88df57..HEAD over 2 changed file(s)\nrest\n"
        live = "clean. git diff 5be26b2068da..HEAD over 0 changed file(s)\nrest\n"
        self.assertEqual(stable(book), stable(live))

    def test_any_other_difference_still_differs(self):
        stable = self._stable()
        book = "verdict: PASS\ngit diff 47422a88df57..HEAD over 2 changed file(s)\n"
        live = "verdict: NO-DATA\ngit diff 47422a88df57..HEAD over 2 changed file(s)\n"
        self.assertNotEqual(stable(book), stable(live))

    def test_sha_masking_never_eats_the_count_or_the_verdict(self):
        """This fixture used to pin the opposite: a pinned range stayed
        byte-compared. The first authenticated CI read (2026-07-31) moved the
        law: the replay builds demo repositories whose commit ids fold in
        dates and identity, so no two machines agree on them, and a mask by
        shape cannot tell a reproducible pinned sha from a machine-dependent
        demo one. Shas mask everywhere now; what this fixture defends is
        that ONLY the sha masks: a differing count, verdict, or any other
        byte beside it must still fail the comparison."""
        stable = self._stable()
        a = "git diff 47422a88df57..f924538 over 2 changed file(s)\nverdict: PASS\n"
        b = "git diff aaaaaaaaaaaa..f924538 over 2 changed file(s)\nverdict: PASS\n"
        self.assertEqual(stable(a), stable(b),
                         "two shas in the same position are the same masked shape")
        c = "git diff 47422a88df57..f924538 over 3 changed file(s)\nverdict: PASS\n"
        self.assertNotEqual(stable(a), stable(c),
                            "the count beside a masked sha must still bite")
        d = "git diff 47422a88df57..f924538 over 2 changed file(s)\nverdict: FAIL\n"
        self.assertNotEqual(stable(a), stable(d),
                            "the verdict beside a masked sha must still bite")

    def test_paths_and_test_ids_mask_but_their_neighbors_still_bite(self):
        stable = self._stable()
        a = "store: /Users/author/Documents/Repo/.sbe ok (__main__.TestEstate.test_x) ... FAIL\n"
        b = "store: /home/runner/work/Repo/.sbe ok (__main__.TestEstate) ... FAIL\n"
        self.assertEqual(stable(a), stable(b),
                         "author path and runner path, old and new unittest id "
                         "formats, are the same masked shape")
        c = "store: /home/runner/work/Repo/.sbe ok (__main__.TestEstate) ... ok\n"
        self.assertNotEqual(stable(a), stable(c),
                            "the outcome beside a masked path must still bite")

    def test_doctor_python_version_masks_but_verdict_and_floor_still_bite(self):
        """The doctor prints the live interpreter version beside the floor:
        "python  PASS  3.9.6 (floor is 3.9)". The version is the machine's,
        so a version-only difference must compare equal; the verdict before
        it and the floor text after it are content and must still fail."""
        stable = self._stable()
        a = "python           PASS     3.9.6 (floor is 3.9)\n"
        b = "python           PASS     3.14.6 (floor is 3.9)\n"
        self.assertEqual(stable(a), stable(b),
                         "the book's 3.9.6 and a newer machine's 3.14.6 are "
                         "the same masked shape")
        c = "python           FAIL     3.9.6 (floor is 3.9)\n"
        self.assertNotEqual(stable(a), stable(c),
                            "the verdict beside a masked version must still bite")
        d = "python           PASS     3.9.6 (floor is 3.10)\n"
        self.assertNotEqual(stable(a), stable(d),
                            "the floor beside a masked version must still bite")


class TestChapterCapabilities(unittest.TestCase):
    """A chapter that declares a required capability is skipped WHOLE and
    LOUDLY on a machine that lacks it, and runs normally where it exists.
    Chapter blocks share one shell, so a skipped setup block would orphan
    every later excerpt; capability is a chapter property by design."""

    def test_a_bare_machine_skips_the_declared_chapters_by_name(self):
        import subprocess
        # THE SCENARIO NEEDS A BARE-BUT-WORKABLE MACHINE, WHICH NOT EVERY HOST
        # CAN BE. `PATH=/usr/bin:/bin` is the canonical bare POSIX box: stripped
        # of anything that would provide `claude` or a vault, yet still holding
        # the shell and tools the un-declared chapters need in order to run and
        # match. That is the whole point of the assertion below, which is that
        # skipping the two DECLARED chapters leaves everything else at zero
        # differing.
        #
        # A host with no POSIX toolchain cannot be put in that state at all.
        # There, those two directories hold nothing, every chapter fails for
        # want of a shell rather than for want of the declared capability, and
        # the run reports every block as differing: 136 of 136, in 1.28 seconds
        # against roughly eleven on a host where the scenario is real. That is
        # the premise being unbuildable, not the behaviour under test failing,
        # so it is skipped there and says why.
        #
        # Measured by asking the filesystem for the directories this scenario
        # names, never by asking for a platform. What is NOT claimed by this
        # skip: the book's transcripts themselves still verify on such a host
        # through the regression eval, which runs the same replay with the real
        # PATH. Only this bare-machine simulation is unavailable.
        for required in ("/usr/bin", "/bin"):
            if not os.path.isdir(required):
                raise unittest.SkipTest(
                    "%s does not exist, so the canonical bare POSIX machine this "
                    "scenario simulates cannot be constructed here" % required)
        env = dict(os.environ, PATH="/usr/bin:/bin", SBE_REPLAY_TIMEOUT="240")
        env.pop("BROTHERSBE_VAULT", None)
        out = subprocess.run([sys.executable,
                              os.path.join(ROOT, "evals", "replay_book.py")],
                             capture_output=True, text=True, env=env, timeout=600)
        text = out.stdout + out.stderr
        self.assertIn("SKIPPED chapter 04-install-day.md", text)
        self.assertIn("requires claude", text)
        self.assertIn("SKIPPED chapter 10-the-vault-and-memory.md", text)
        self.assertIn("requires vault", text)
        self.assertIn("SKIPPED chapter 15-the-platform-lead-deep-dive.md", text)
        self.assertIn("requires gh", text)
        self.assertIn(", 0 differ", text.strip().splitlines()[-1],
                      "skipping must leave nothing differing: %s" % text[-400:])

    def test_the_caret_underline_lines_of_newer_interpreters_mask(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "replay_book", os.path.join(ROOT, "evals", "replay_book.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old = "AssertionError: boom\n    self.assertEqual(a, b)\n"
        new_style = ("AssertionError: boom\n    self.assertEqual(a, b)\n"
                     "    ^^^^^^^^^^^^^^^^^^^^^^\n")
        self.assertEqual(mod.stable(old), mod.stable(new_style),
                         "an underline-only line is interpreter decoration")
        other = "AssertionError: different\n    self.assertEqual(a, b)\n"
        self.assertNotEqual(mod.stable(old), mod.stable(other),
                            "the message beside a masked caret line still bites")

    def test_a_mid_text_underline_line_vanishes_whole(self):
        """The first mask stripped only the glyphs, which worked by accident
        when the underline was the text's last line (the trailing whitespace
        class swallowed the final newline) and left a blank line behind
        everywhere else. The two 3.14 CI legs (2026-07-31) failed on exactly
        that: a mid-traceback underline whose leftover blank line no
        3.9-recorded excerpt could ever contain. Calibrated both ways: the
        3.13 tilde-plus-caret shape masks wholly, and a line carrying any
        byte besides underline glyphs still bites."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "replay_book", os.path.join(ROOT, "evals", "replay_book.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        floor = ("Traceback (most recent call last):\n"
                 "    self.assertIn(a, b)\n"
                 "AssertionError: boom\n")
        newer = ("Traceback (most recent call last):\n"
                 "    self.assertIn(a, b)\n"
                 "    ~~~~~~~~~~~~~^^^^^^\n"
                 "AssertionError: boom\n")
        self.assertEqual(mod.stable(floor), mod.stable(newer),
                         "a mid-text underline line must vanish with its "
                         "newline, leaving no blank line behind")
        self.assertEqual(mod.stable(newer), mod.stable(mod.stable(newer)),
                         "the mask stays idempotent")
        content = ("Traceback (most recent call last):\n"
                   "    self.assertIn(a, b)\n"
                   "    ^^^^ partial, carries content\n"
                   "AssertionError: boom\n")
        self.assertNotEqual(mod.stable(floor), mod.stable(content),
                            "a line with any byte besides underline glyphs "
                            "is content and must still bite")


class TestExplainerSelfContained(unittest.TestCase):
    """The beginner explainer is a single offline page: this guards that it
    stays large enough to be real content, carries no external script,
    image, stylesheet, or media reference anywhere (plain anchor links to
    GitHub are the one allowed exception), stays free of the two banned
    dash characters, and declares both a light and a dark theme so an
    explicit toggle can win either way."""

    EXPLAINER = os.path.join(ROOT, "docs", "explainer", "index.html")

    def _load(self):
        with io.open(self.EXPLAINER, encoding="utf-8") as handle:
            html = handle.read()
        size = os.path.getsize(self.EXPLAINER)
        return self.EXPLAINER, html, size

    def test_the_file_exists_and_is_substantial(self):
        path, html, size = self._load()
        self.assertTrue(os.path.exists(path), "docs/explainer/index.html is missing")
        self.assertGreater(size, 5000,
                           "the explainer is suspiciously thin: %d bytes" % size)

    def test_no_external_script_image_link_or_source_references(self):
        import re
        path, html, size = self._load()
        tag_pattern = re.compile(r"<(script|img|link|source)\b([^>]*)>", re.IGNORECASE)
        attr_pattern = re.compile(r"(src|href)\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
        offenders = []
        for tag_match in tag_pattern.finditer(html):
            tag_name = tag_match.group(1)
            for attr_name, value in attr_pattern.findall(tag_match.group(2)):
                if value.lower().startswith("http://") or value.lower().startswith("https://"):
                    offenders.append("<%s %s=%s>" % (tag_name, attr_name, value))
        self.assertEqual(offenders, [],
                         "external script/img/link/source reference(s) found: %s" % offenders)
        css_import = re.search(r"@import\s+[\"']?https?://", html, re.IGNORECASE)
        css_url = re.search(r"url\(\s*[\"']?https?://", html, re.IGNORECASE)
        self.assertIsNone(css_import, "an @import pulls from an external http(s) URL")
        self.assertIsNone(css_url, "a CSS url() points at an external http(s) URL")

    def test_zero_banned_dash_characters(self):
        path, html, size = self._load()
        em_dash_count = html.count(u"\u2014")
        en_dash_count = html.count(u"\u2013")
        self.assertEqual(em_dash_count, 0, "an em dash (U+2014) slipped into the explainer")
        self.assertEqual(en_dash_count, 0, "an en dash (U+2013) slipped into the explainer")

    def test_declares_both_light_and_dark_themes(self):
        path, html, size = self._load()
        self.assertIn("prefers-color-scheme", html,
                     "no system-preference dark theme rule found")
        self.assertIn("data-theme=", html,
                     "no explicit data-theme toggle override found")


if __name__ == "__main__":
    unittest.main(verbosity=2)
