#!/usr/bin/env python3
"""Every block the slicer KEEPS is whole and byte-identical, and an impossible budget raises.

WHY THIS ONE. slicer.py feeds the build brief for every module too large to send whole, and the
worker's patch is a unique-find against the REAL file. So the slice is a promise: what is shown
is what is on disk, to the byte. Break that and nothing errors. The model reads plausible code,
writes a plausible find, and grade_build reports "find matches 0 times" on every build of every
lane that touches a big module. The estate has already paid for the neighbouring version of this
failure: the module's own comment records three sub units blocked for a day because the slicer
refused instead of dropping more blocks.

Two properties, and the first is the one a naive test misses. Checking that each kept chunk is a
SUBSTRING of the source does not catch a truncation, because a prefix of a contiguous slice is
still a substring. So this asserts the COMPLETE source segment of every block that was not
dropped appears in the output: kept must mean whole, never merely recognisable.

The second is the failure DIRECTION. When even the header does not fit, the slicer must raise.
Returning a shortened string there would hand a worker a file it cannot patch, with no sign
anything was wrong.

Run: python3 scripts/test_slicer_guard.py
"""
import ast
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import slicer  # noqa: E402

MARKER = re.compile(r"^# \[SLICED OUT, not shown: (\w+), lines \d+ to \d+\]$", re.M)


def module_source(names, body_lines):
    """A synthetic module with a header and one padded top-level function per name."""
    out = ["import os\nimport sys\n\nCONST = 'header constant'\n\n"]
    for n in names:
        out.append("def %s(argument):\n" % n)
        out.extend("    # %s padding line %03d, kept here to give the block a real size\n" % (n, i)
                   for i in range(body_lines))
        out.append("    return %r\n\n\n" % n)
    return "".join(out)


def segments(src):
    """The exact source text of every top-level def, as the file holds it."""
    lines = src.splitlines(keepends=True)
    return {n.name: "".join(lines[n.lineno - 1:n.end_lineno])
            for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}


class KeptBlocksAreWhole(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="slicer-guard-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, src):
        path = os.path.join(self.dir, "big_module.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        return path

    def check(self, src, out, dropped):
        """Kept means byte-identical and WHOLE; dropped means named, never silently missing."""
        seg = segments(src)
        self.assertEqual(sorted(dropped), sorted(set(dropped)), "a block was reported dropped twice")
        for name, text in seg.items():
            if name in dropped:
                self.assertNotIn(text, out, "%s is named as dropped but its body was shipped" % name)
                self.assertIn(name, MARKER.findall(out), "%s vanished with no marker naming it" % name)
            else:
                self.assertIn(text, out, "%s was kept but not whole and byte-identical" % name)
        self.assertIn("CONST = 'header constant'", out, "the header was lost")
        # the result must still be readable Python: a cut inside a block would not be
        ast.parse(out)

    def test_a_file_under_budget_comes_back_untouched(self):
        src = module_source(["alpha", "beta"], 3)
        out, dropped = slicer.slice_source(self.write(src), "alpha", 10 ** 6)
        self.assertEqual(out, src)
        self.assertEqual(dropped, [])

    def test_blocks_the_spec_does_not_name_are_dropped_and_named(self):
        src = module_source(["alpha", "beta", "gamma"], 40)
        out, dropped = slicer.slice_source(self.write(src), "the spec talks about alpha only", len(src) // 2)
        self.assertEqual(sorted(dropped), ["beta", "gamma"])
        self.assertLessEqual(len(out), len(src) // 2)
        self.check(src, out, dropped)

    def test_a_forcing_budget_drops_whole_blocks_and_never_cuts_one(self):
        # every block is named by the spec, so the only way to fit is to force drops
        src = module_source(["alpha", "beta", "gamma", "delta"], 60)
        spec = "alpha beta gamma delta all matter here"
        budget = len(src) // 3
        out, dropped = slicer.slice_source(self.write(src), spec, budget)
        self.assertLessEqual(len(out), budget, "the budget was not met")
        self.assertTrue(dropped, "nothing was dropped yet the file shrank")
        self.check(src, out, dropped)

    def test_relevance_decides_what_survives_a_forcing_budget(self):
        src = module_source(["alpha", "beta", "gamma", "delta"], 60)
        spec = "alpha alpha alpha alpha alpha is what this sub unit changes; beta gamma delta"
        out, dropped = slicer.slice_source(self.write(src), spec, len(src) // 3)
        self.assertNotIn("alpha", dropped, "the most named block was dropped first: %s" % dropped)
        self.check(src, out, dropped)

    def test_an_impossible_budget_raises_instead_of_returning_a_short_file(self):
        # a worker handed a truncated file writes a find that cannot apply, and nothing says why
        src = module_source(["alpha"], 40)
        path = self.write(src)
        with self.assertRaises(ValueError) as caught:
            slicer.slice_source(path, "alpha", 20)
        self.assertIn("big_module.py", str(caught.exception))

    def test_a_module_docstring_is_the_first_block_forced_out_and_named_functions_survive(self):
        """MEASURED on run 2 of 2026-09-24: scripts/jev_seam.py with every function sliced out still weighed 53259
        bytes against a 41865 byte share, 38748 of them its module docstring, so the one module D3.7's tests import
        read NOT SHOWN and eleven builds guessed its shapes. Dropping the docstring LAST was no better: every named
        function had been forced out first to make room for prose. No patch targets a docstring: it goes first."""
        essay = '"""' + ("this is the module essay, line of prose that a worker never patches\n" * 60) + '"""\n'
        src = essay + module_source(["alpha", "beta"], 10)
        path = self.write(src)
        budget = len(src) - len(essay) + 200            # fits only once the docstring is gone, with room for nothing else
        out, dropped = slicer.slice_source(path, "alpha beta", budget)
        self.assertLessEqual(len(out), budget)
        self.assertNotIn("module essay", out)
        self.assertIn("MODULE DOCSTRING SLICED OUT", out)
        self.assertEqual(dropped, ["<module docstring>"], "a named function was forced out to make room for prose")
        self.check(src, out, [])
        # under budget, nothing is touched, docstring included
        out2, dropped2 = slicer.slice_source(path, "alpha beta", len(src) + 1)
        self.assertEqual((out2, dropped2), (src, []))

    def test_a_large_module_level_constant_is_sliced_out_by_its_lines_and_imports_never_are(self):
        """2026-09-24 14:3x: loop_bridge.py's module level code alone outweighed its share by 474 bytes and the whole
        file read NOT SHOWN. A constant is a block too: dropped largest first, marked by its lines, imports kept."""
        table = "TABLE = {\n" + "".join("    %r: %r,\n" % ("key%03d" % i, "v" * 40) for i in range(60)) + "}\n\n"
        src = "import os\nimport sys\n\n" + table + module_source(["alpha"], 10)
        path = self.write(src)
        budget = len(src) - len(table) + 200
        out, dropped = slicer.slice_source(path, "alpha", budget)
        self.assertLessEqual(len(out), budget)
        self.assertNotIn("key059", out)
        self.assertIn("MODULE LEVEL STATEMENT SLICED OUT", out)
        self.assertTrue(any(d.startswith("<module level statement lines") for d in dropped), dropped)
        self.assertIn("import os\nimport sys\n", out)
        self.check(src, out, [d for d in dropped if not d.startswith("<")])

    def test_an_unreadable_file_raises_rather_than_returning_empty(self):
        with self.assertRaises(OSError):
            slicer.slice_source(os.path.join(self.dir, "there-is-no-such-module.py"), "alpha", 1000)


if __name__ == "__main__":
    unittest.main()
