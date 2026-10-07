#!/usr/bin/env python3
"""FX-09.3: no file leaves a build brief without its name.

WHY: the brief builder shared its byte budget between the files a section names, but every file block also spends a
header and a newline that the shares never counted, so the last block overflowed by a few dozen bytes and was dropped
with NO marker (reproduced: three 95000 byte files, two blocks written, brief_check B1 REFUSED the third). Text files
were cut by characters, so non ASCII text could exceed its share in bytes and be dropped the same way, and a file that
was not UTF-8 crashed the builder. Every test drives build_brief.py as the loop does (a subprocess, cwd at a temp tree,
its own HOME, plan and runs folder) and reads the brief back through brief_check.main, the gate the runner calls.
Run: python3 -B scripts/test_build_brief_keeps_every_file.py"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
BUILD_BRIEF = os.path.join(LOOP, "build_brief.py")
sys.path.insert(0, LOOP)
import brief_check  # noqa: E402
import brief_fit  # noqa: E402

OVERFLOW_WORDS = "NOT SHOWN: this file did not fit its share of the brief"
WBS_JSON = ('{"initiative": "the launch", "units": [{"id": "U", "title": "a unit", "objective": "do the thing", '
            '"done_check": "python3 -B scripts/test_u.py", "checker": "tests"}]}')


class BriefTree(object):
    """A temp tree the brief builder runs in: a spec, a plan, an empty private names list, an empty runs folder.
    Each fixture trips ONE guard: the plan row makes B5 pass, the section's done check makes B4 pass, and the
    empty names list keeps the private guard quiet unless a test is about it."""

    def __init__(self, case):
        self.root = tempfile.mkdtemp(prefix="brief-keeps-")
        case.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "_home")
        os.makedirs(self.home)
        os.makedirs(os.path.join(self.root, "_runs"))
        self.write("_home/.brothersbe-private-names", "")
        self.write("_plan.json", WBS_JSON)

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        return rel

    def spec(self, section_body):
        return self.write("spec.md", "# U\n\nThe unit preamble.\n\n### U.1 the sub unit\n" + section_body
                          + "\nTests: `scripts/test_u.py` NEW.\nDone check: `python3 -B scripts/test_u.py`\n\n"
                          "### U.2 another\nnothing here\n")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("BROTHER_") and k not in ("HOME", "PYTHONPATH")}
        env.update(HOME=self.home, BROTHER_WBS=os.path.join(self.root, "_plan.json"),
                   BROTHER_UNIT_RUNS=os.path.join(self.root, "_runs"), PYTHONDONTWRITEBYTECODE="1")
        env.update(extra)
        return env

    def build(self, out="out.md", **extra):
        return subprocess.run([sys.executable, BUILD_BRIEF, "U", "U.1", "spec.md", out], cwd=self.root,
                              env=self.env(**extra), capture_output=True, text=True, timeout=300)

    def start(self, out, **extra):
        return subprocess.Popen([sys.executable, BUILD_BRIEF, "U", "U.1", "spec.md", out], cwd=self.root,
                                env=self.env(**extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read(self, rel="out.md"):
        with open(os.path.join(self.root, rel), "rb") as fh:
            return fh.read().decode("utf-8")

    def gate(self, out="out.md"):
        buf = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with redirect_stdout(buf):
                code = brief_check.main(["U", "U.1", "spec.md", out])
        finally:
            os.chdir(cwd)
        return code, buf.getvalue()


def blocks(brief):
    return re.findall(r"^===== FILE: (.+?) =====$", brief, flags=re.M)


def _lift_place():
    """build_brief.py works at import, so `place` is lifted by source, the same honest caveat as
    test_build_brief_corrections._load; the pinned call site below keeps deleting its use from staying green."""
    with open(BUILD_BRIEF, encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("OVERFLOW = ")
    end = src.index("def shares(")
    ns = {"os": os, "sys": sys, "existing": [], "budget": 10 ** 9}
    exec(compile(src[start:end], "build_brief_place", "exec"), ns)
    return ns, src


class NoFileLeavesWithoutItsName(unittest.TestCase):
    def setUp(self):
        self.t = BriefTree(self)

    def test_the_last_file_is_never_dropped_three_large_files(self):
        for name in ("a", "b", "c"):
            self.t.write("docs/%s.md" % name, ("%s line of prose\n" % name) * (95000 // 17))
        self.t.spec("Files: `docs/a.md`, `docs/b.md` and `docs/c.md` existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        self.assertEqual(blocks(brief), ["docs/a.md", "docs/b.md", "docs/c.md"])
        # and the names were paid for out of the budget, never on top of it (the builder's own cap, under the gate's)
        self.assertLessEqual(len(brief.encode("utf-8")), min(brief_check.BUDGET, brief_fit.CAP - brief_fit.NOTE_FLOOR))
        code, out = self.t.gate()
        self.assertIn("B1 NAMED SHOWN    PASS", out)
        self.assertEqual(code, 0, out)

    def test_every_existing_path_has_exactly_one_block(self):
        names = []
        for i in range(40):
            names.append(self.t.write("scripts/s%02d.py" % i, "def f%d():\n    return %d\n" % (i, i)))
        for big in ("big1", "big2"):
            body = "".join("def %s_%d(x):\n    return x + %d  # %s\n\n" % (big, i, i, "p" * 60) for i in range(1600))
            names.append(self.t.write("scripts/%s.py" % big, body))
            self.t.write("scripts/test_%s_more.py" % big, "import %s\n" % big + "x = 1  # %s\n" % ("q" * 70) * 900)
        self.t.spec("Files: " + ", ".join("`%s`" % n for n in names) + " existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        existing = [p for p in r.stdout.split() if os.path.isfile(os.path.join(self.t.root, p))]
        self.assertGreaterEqual(len(existing), 44)
        shown = blocks(brief)
        for p in existing:
            self.assertEqual(shown.count(p), 1, p)
        self.assertLessEqual(len(brief.encode("utf-8")), brief_check.BUDGET)

    def test_one_file_gets_exactly_one_block(self):
        # a Python file whose CHARACTERS fit its share and whose BYTES do not: the slicer counts characters
        self.t.write("scripts/wide.py", "# " + "\u00e9" * 100000 + "\nX = 1\n")
        self.t.spec("File: `scripts/wide.py` existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        self.assertEqual(blocks(brief), ["scripts/wide.py"])
        self.assertIn(OVERFLOW_WORDS, brief)
        self.assertLessEqual(len(brief.encode("utf-8")), brief_check.BUDGET)

    def test_an_earlier_file_never_spends_a_later_files_name_reservation(self):
        # a Python file whose CHARACTERS fit its share and whose BYTES fit the whole budget but not what is left after
        # the later files' reservations: it is NAMED, and every later file keeps the room its share gave it
        later = [self.t.write("docs/l%02d.md" % i, ("later %02d line\n" % i) * 20) for i in range(40)]
        self.t.write("scripts/w.py", "X = 1\n")
        self.t.spec("Files: `scripts/w.py` then " + ", ".join("`%s`" % p for p in later) + " existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        mark = "\n\nTHE REAL FILES:\n"
        first = self.t.read()
        body = first[:first.index(mark) + len(mark)]
        limit = min(brief_check.BUDGET, brief_fit.CAP - brief_fit.NOTE_FLOOR)
        budget = limit - len(body.encode("utf-8"))
        overflow = _lift_place()[0]["OVERFLOW"]
        owed = sum(len(("===== FILE: %s =====\n" % p).encode()) + 1 + len(overflow.encode()) for p in later)
        head = "===== FILE: scripts/w.py =====\n"
        n = (budget - owed // 2 - len(head) - len("# \nX = 1\n") - 1) // 2   # the chunk lands inside (budget - owed, budget]
        self.t.write("scripts/w.py", "# " + "é" * n + "\nX = 1\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        self.assertTrue(brief.startswith(body), "the same body, so the same budget")
        self.assertEqual(blocks(brief), ["scripts/w.py"] + later)
        self.assertTrue(brief.split(head, 1)[1].startswith("[NOT SHOWN: this file did not fit"), "w.py is named")
        for i, p in enumerate(later):
            self.assertIn("===== FILE: %s =====\n%s" % (p, ("later %02d line\n" % i) * 20), brief)
        self.assertLessEqual(len(brief.encode("utf-8")), limit)

    def test_a_non_ascii_text_file_is_cut_by_bytes_and_marked(self):
        raw = ("\u00e9" * 120000).encode("utf-8")
        self.t.write("docs/wide.md", raw)
        self.t.spec("File: `docs/wide.md` existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        self.assertEqual(blocks(brief), ["docs/wide.md"])
        m = re.search(r"\[NOT SHOWN past byte (\d+) of (\d+): the rest of this file exists and is real\]", brief)
        self.assertIsNotNone(m, "a cut text file is marked")
        self.assertEqual(int(m.group(2)), len(raw))
        shown = brief.split("===== FILE: docs/wide.md =====\n", 1)[1].split("\n[NOT SHOWN past byte", 1)[0]
        self.assertLessEqual(len(shown.encode("utf-8")), int(m.group(1)))
        self.assertEqual(shown, "\u00e9" * len(shown), "the cut never splits a character")
        self.assertLessEqual(len(brief.encode("utf-8")), brief_check.BUDGET)

    def test_a_file_that_is_not_utf8_is_named_not_crashed(self):
        self.t.write("docs/binary.json", b"\xff\xfe{\"a\": 1}\n")
        self.t.spec("File: `docs/binary.json` existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        brief = self.t.read()
        self.assertEqual(blocks(brief), ["docs/binary.json"])
        block = brief.split("===== FILE: docs/binary.json =====\n", 1)[1]
        self.assertTrue(block.startswith("[NOT SHOWN: this file is not UTF-8 text"), block[:200])

    def test_too_many_files_to_name_is_NO_DATA(self):
        names = [self.t.write("docs/%s%03d.md" % ("d" * 80, i), "x\n") for i in range(600)]
        self.t.spec("Files: " + " ".join("`%s`" % n for n in names) + " existing.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 2, r.stdout[-300:] + r.stderr)
        self.assertIn("NO-DATA: 600 files cannot all be named", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.t.root, "out.md")))

    def test_a_section_naming_nothing_still_builds(self):
        self.t.spec("This section names no file at all.\n")
        r = self.t.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(blocks(self.t.read()), [])

    def test_a_rebuild_into_the_same_path_is_identical(self):
        self.t.write("docs/a.md", "alpha\n" * 5000)
        self.t.spec("File: `docs/a.md` existing.\n")
        self.assertEqual(self.t.build().returncode, 0)
        first = self.t.read()
        self.assertEqual(self.t.build().returncode, 0)
        self.assertEqual(self.t.read(), first)
        self.assertEqual(blocks(first), ["docs/a.md"])

    def test_two_concurrent_builds_of_one_section_are_identical(self):
        self.t.write("docs/a.md", "alpha\n" * 5000)
        self.t.write("docs/b.md", "beta\n" * 5000)
        self.t.spec("Files: `docs/a.md` and `docs/b.md` existing.\n")
        p1, p2 = self.t.start("one.md"), self.t.start("two.md")
        for p in (p1, p2):
            p.communicate(timeout=300)
            self.assertEqual(p.returncode, 0)
        self.assertEqual(self.t.read("one.md"), self.t.read("two.md"))


class ThePlacementDecision(unittest.TestCase):
    def setUp(self):
        self.ns, self.src = _lift_place()

    def test_place_shows_a_chunk_that_fits_exactly(self):
        chunk = "===== FILE: docs/a.md =====\n\u00e9\u00e9 text\n"
        size = len(chunk.encode("utf-8"))
        self.assertEqual(self.ns["place"]("docs/a.md", chunk, size), chunk)

    def test_place_names_a_chunk_one_byte_over(self):
        chunk = "===== FILE: docs/a.md =====\ntext\n"
        out = self.ns["place"]("docs/a.md", chunk, len(chunk.encode("utf-8")) - 1)
        self.assertEqual(out, "===== FILE: docs/a.md =====\n%s\n" % self.ns["OVERFLOW"])
        self.assertIn(OVERFLOW_WORDS, out)

    def test_the_placement_is_wired_and_the_silent_drop_is_gone(self):
        self.assertIn("chunk = place(p, chunk,", self.src)
        self.assertNotIn("continue\n    body += chunk", self.src)


if __name__ == "__main__":
    unittest.main()
