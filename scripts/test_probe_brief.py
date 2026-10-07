"""What the probe brief builder must refuse, and how each refusal shows itself.

Every test here owns its tree: fixtures are written into a temp base directory
this test creates and removes, so no shared file can make a verdict. The
private names list is pointed at a file this test writes, because the reader
fails closed: on a machine with no names file every brief is blocked, which is
the right behaviour and the wrong fixture.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G  # noqa: E402
import probe_brief as B  # noqa: E402

#: A token this test invents. It is not a real private name. It is only in the
#: names list this test writes, so a hit here can only come from that list.
PRIVATE_TOKEN = "zeta" + "-token"

#: Clean fixture text: no private term in it, so a block proves something else.
SPEC_TEXT = "# specification\n\nRun the lane one probe and report.\n"

#: Values no argument of this module may accept: None, a wrong type, a number
#: where a string belongs, bytes, an empty container, an unhashable value.
HOSTILE = (None, 7, True, float("nan"), b"text", [], {}, set())


def _rmtree(path):
    """Remove a temp tree without shutil, which this estate's screen forbids."""
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


class BriefCase(unittest.TestCase):
    """A temp tree and a readable private names list, both owned by this test."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="probe-brief-")
        self.old_names = os.environ.get(G.PRIVATE_NAMES_ENV)
        self.names = self._write("names.txt",
                                 b"# synthetic list\n"
                                 + PRIVATE_TOKEN.encode("utf-8") + b"\n")
        os.environ[G.PRIVATE_NAMES_ENV] = self.names
        self.spec = self._write("spec.md", SPEC_TEXT.encode("utf-8"))

    def tearDown(self):
        if self.old_names is None:
            os.environ.pop(G.PRIVATE_NAMES_ENV, None)
        else:
            os.environ[G.PRIVATE_NAMES_ENV] = self.old_names
        _rmtree(self.base)

    def _write(self, name, data):
        path = os.path.join(self.base, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _path(self, name):
        return os.path.join(self.base, name)

    def _read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def _leftovers(self):
        return [name for name in os.listdir(self.base) if ".part-" in name]


class TheBriefIsWrittenWholeOrNotAtAll(BriefCase):
    """R12: the brief text is written to out_path and returned."""

    def test_a_written_brief_holds_the_lane_and_the_specification(self):
        out = self._path("brief.txt")
        text = B.build_brief(self.spec, "lane-one", "builds/one.json", out)
        self.assertFalse(text.startswith("REFUSED"), text)
        self.assertEqual(self._read(out).decode("utf-8"), text)
        self.assertIn("lane-one", text)
        self.assertIn("Run the lane one probe", text)

    def test_the_out_file_is_replaced_in_one_rename(self):
        out = self._write("brief.txt", b"an earlier brief, now stale")
        text = B.build_brief(self.spec, "lane-one", "builds/one.json", out)
        self.assertEqual(self._read(out).decode("utf-8"), text)
        self.assertEqual(self._leftovers(), [])

    def test_a_missing_specification_raises_value_error(self):
        out = self._path("brief.txt")
        with self.assertRaises(ValueError):
            B.build_brief(self._path("absent.md"), "lane-one",
                          "builds/one.json", out)
        self.assertFalse(os.path.exists(out))

    def test_a_specification_that_is_a_directory_raises_value_error(self):
        out = self._path("brief.txt")
        with self.assertRaises(ValueError):
            B.build_brief(self.base, "lane-one", "builds/one.json", out)
        self.assertFalse(os.path.exists(out))

    def test_a_specification_that_is_not_utf8_raises_value_error(self):
        spec = self._write("bad.md", b"# specification\n\xff\xfe\n")
        out = self._path("brief.txt")
        with self.assertRaises(ValueError):
            B.build_brief(spec, "lane-one", "builds/one.json", out)
        self.assertFalse(os.path.exists(out))

    def test_an_empty_lane_is_refused_and_nothing_is_written(self):
        for index, lane in enumerate(("", "   ", "\t\n")):
            out = self._path("lane-%d.txt" % index)
            text = B.build_brief(self.spec, lane, "builds/one.json", out)
            self.assertTrue(text.startswith("REFUSED"), repr(lane))
            self.assertFalse(os.path.exists(out), repr(lane))

    def test_a_build_path_with_a_newline_is_refused(self):
        out = self._path("brief.txt")
        for build_path in ("one\n.json", "one\r.json", "one\x00.json"):
            text = B.build_brief(self.spec, "lane-one", build_path, out)
            self.assertTrue(text.startswith("REFUSED"), repr(build_path))
        self.assertFalse(os.path.exists(out))

    def test_a_brief_over_the_size_limit_is_refused(self):
        spec = self._write("huge.md", b"x" * 200000)
        out = self._path("brief.txt")
        text = B.build_brief(spec, "lane-one", "builds/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), "200000 bytes must refuse")
        self.assertFalse(os.path.exists(out))

    def test_a_brief_under_the_size_limit_is_written(self):
        spec = self._write("long.md", b"y" * 180000)
        out = self._path("brief.txt")
        text = B.build_brief(spec, "lane-one", "builds/one.json", out)
        self.assertFalse(text.startswith("REFUSED"), text[:200])
        self.assertTrue(os.path.exists(out))

    def test_a_build_that_does_not_exist_is_still_described(self):
        out = self._path("brief.txt")
        text = B.build_brief(self.spec, "lane-one", "builds/absent.json", out)
        self.assertFalse(text.startswith("REFUSED"), text)
        self.assertIn("builds/absent.json", text)


class ThePrivateTermScreenFailsClosed(BriefCase):
    """R13: a private term blocks, and a bare network word is not a term."""

    def test_bare_word_not_blocked(self):
        text = ("the probe opens a socket to https://example.invalid, reads "
                "one line and closes it")
        self.assertFalse(B.private_terms_block(text))

    def test_a_private_term_is_blocked(self):
        self.assertTrue(B.private_terms_block("all about %s here"
                                              % PRIVATE_TOKEN))

    def test_a_private_term_is_found_whatever_its_case(self):
        self.assertTrue(B.private_terms_block(PRIVATE_TOKEN.upper()))

    def test_an_unreadable_names_file_blocks(self):
        os.environ[G.PRIVATE_NAMES_ENV] = self._path("absent-names.txt")
        self.assertTrue(B.private_terms_block("nothing to see here"))

    def test_a_hostile_text_blocks_rather_than_crashing(self):
        for value in HOSTILE:
            self.assertTrue(B.private_terms_block(value), repr(value))

    def test_a_private_term_in_the_specification_blocks_the_brief(self):
        spec = self._write("blocked.md",
                           ("about %s\n" % PRIVATE_TOKEN).encode("utf-8"))
        out = self._path("brief.txt")
        text = B.build_brief(spec, "lane-one", "builds/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(out))

    def test_a_private_term_in_the_build_path_blocks_the_brief(self):
        out = self._path("brief.txt")
        text = B.build_brief(self.spec, "lane-one",
                             PRIVATE_TOKEN + "/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(out))

    def test_a_private_term_in_the_lane_blocks_the_brief(self):
        out = self._path("brief.txt")
        text = B.build_brief(self.spec, "lane " + PRIVATE_TOKEN,
                             "builds/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(out))

    def test_an_unreadable_names_file_blocks_the_brief(self):
        os.environ[G.PRIVATE_NAMES_ENV] = self._path("absent-names.txt")
        out = self._path("brief.txt")
        text = B.build_brief(self.spec, "lane-one", "builds/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(out))


class HostileInputIsRefusedAndNeverCrashes(BriefCase):
    """Every public argument is fed None, a wrong type, empty and NaN."""

    def test_a_hostile_lane_is_refused_and_nothing_is_written(self):
        out = self._path("brief.txt")
        for value in HOSTILE:
            text = B.build_brief(self.spec, value, "builds/one.json", out)
            self.assertTrue(text.startswith("REFUSED"), repr(value))
        self.assertFalse(os.path.exists(out))

    def test_a_hostile_spec_path_raises_value_error(self):
        out = self._path("brief.txt")
        for value in HOSTILE:
            with self.assertRaises(ValueError):
                B.build_brief(value, "lane-one", "builds/one.json", out)
        self.assertFalse(os.path.exists(out))

    def test_hostile_build_and_out_paths_are_refused(self):
        out = self._path("brief.txt")
        for value in HOSTILE:
            first = B.build_brief(self.spec, "lane-one", value, out)
            self.assertTrue(first.startswith("REFUSED"), repr(value))
            second = B.build_brief(self.spec, "lane-one", "builds/one.json",
                                   value)
            self.assertTrue(second.startswith("REFUSED"), repr(value))
        self.assertFalse(os.path.exists(out))

    def test_an_out_path_that_is_a_directory_is_refused(self):
        directory = self._path("outdir")
        os.mkdir(directory)
        text = B.build_brief(self.spec, "lane-one", "builds/one.json",
                             directory)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertTrue(os.path.isdir(directory))
        self.assertEqual(self._leftovers(), [])

    def test_an_out_directory_that_is_missing_is_refused(self):
        out = os.path.join(self.base, "absent-dir", "brief.txt")
        text = B.build_brief(self.spec, "lane-one", "builds/one.json", out)
        self.assertTrue(text.startswith("REFUSED"), text)
        self.assertFalse(os.path.exists(out))


class TheExitCodeSaysWhatHappened(BriefCase):
    """R15: 0 on a written brief, 1 on refusal, 2 on missing arguments."""

    def test_a_written_brief_is_zero(self):
        out = self._path("brief.txt")
        code = B.main([self.spec, "lane-one", "builds/one.json", out])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out))

    def test_a_refused_brief_is_one(self):
        out = self._path("brief.txt")
        code = B.main([self.spec, "", "builds/one.json", out])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out))

    def test_an_unreadable_specification_is_one(self):
        out = self._path("brief.txt")
        code = B.main([self._path("absent.md"), "lane-one",
                       "builds/one.json", out])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out))

    def test_missing_arguments_are_two(self):
        out = self._path("brief.txt")
        for argv in ([], ["a"], ["a", "b"], ["a", "b", "c"],
                     [self.spec, "lane-one", "builds/one.json", out,
                      "extra"]):
            self.assertEqual(B.main(argv), 2, repr(argv))

    def test_a_hostile_argv_is_two_and_never_crashes(self):
        for argv in (7, True, float("nan"), "args", {}, b"args",
                     [None, "lane-one", "builds/one.json", "out.txt"],
                     [7, "lane-one", "builds/one.json", "out.txt"],
                     [self.spec, None, "builds/one.json", "out.txt"]):
            self.assertEqual(B.main(argv), 2, repr(argv))

    def test_main_without_an_argv_reads_the_real_command_line(self):
        old = list(sys.argv)
        sys.argv = ["probe_brief.py"]
        try:
            self.assertEqual(B.main(None), 2)
        finally:
            sys.argv[:] = old


if __name__ == "__main__":
    unittest.main()
