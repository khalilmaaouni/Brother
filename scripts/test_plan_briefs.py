#!/usr/bin/env python3
"""PG1.e: tests for scripts/plan_briefs.py, the writer briefs, the wave table and the report filing.

Every case builds its own fixture tree (a unit spec with sub unit sections, a batch file in the shape
scripts/plan_gen.py writes) in a temporary directory and drives the real entry point plan_briefs.main as well as the
functions. HOME is an empty temporary directory for the whole run; no case needs ps, gh, the network, a model or the
real docs/plan. The one case that reads the real 1.1.4 batch file is skipped on a tree without docs/plan/specs. No
em or en dashes and no clock text are typed in this file: such strings are built in code.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import plan_briefs as PB  # noqa: E402

HUB = os.path.isdir(os.path.join(ROOT, "docs", "plan", "specs"))
CLOCK = "09" + ":" + "30"
SPEC = "\n".join([
    "# AA1: title", "", "## 1. Measured baseline", "", "baseline text", "", "## 2. Sub-units", "",
    "### AA1.a first sub unit", "", "Files: `scripts/aa1.py` (NEW).", "", "Done check: `python3 -B scripts/test_aa1.py`", "",
    "#### Edges and failures", "", "WRITER: edges", "",
    "### AA1.b second sub unit", "", "Done check: `python3 -B scripts/test_aa1b.py`", "",
    "### AA1.a1 a sibling whose id starts the same", "", "other text", "",
    "## 3. Parent close", "", "close text", ""])
PREAMBLE = "SHARED PREAMBLE: be exact."


def batch(bid, subs=None, units=None, **kw):
    b = {"id": bid, "name": "name " + bid, "units": units or ["AA1"], "sub_units": subs or [bid],
         "section": {"file": "docs/plan/specs/AA1.md", "sub": (subs or [bid])[0]},
         "writes": ["docs/plan/specs/1.0.0/pack/%s.md" % bid], "reads": [], "cached_reads": [],
         "brief": "docs/plan/specs/1.0.0/briefs/%s.md" % bid, "report": "docs/plan/specs/1.0.0/REPORT-%s.md" % bid,
         "done_check": "python3 -B scripts/test_aa1.py"}
    b.update(kw)
    return b


class Fix:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pg1e_test_")
        self._env = {k: os.environ.get(k) for k in ("HOME", PB.WIDTH_ENV)}
        os.environ["HOME"] = os.path.join(self.tmp, "emptyhome")  # an empty HOME: nothing may lean on this laptop
        os.makedirs(os.environ["HOME"])
        os.environ.pop(PB.WIDTH_ENV, None)
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs", "1.0.0"))
        self.put("docs/plan/specs/AA1.md", SPEC)
        self.assign = os.path.join(self.root, "docs/plan/specs/1.0.0/WRITER-ASSIGNMENTS.json")
        self.out = os.path.join(self.root, "docs/plan/specs/1.0.0/briefs")

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def write_batches(self, batches, fill=True, **extra):
        """The batch file; with `fill` the unit spec also gets a section for every sub unit the batches name."""
        if fill:
            with open(os.path.join(self.root, "docs/plan/specs/AA1.md"), encoding="utf-8") as f:
                text = f.read()
            for b in batches:
                for sub in (b.get("sub_units") or []):
                    if isinstance(sub, str) and PB.section_of(text, sub) is None:
                        text += "### %s generated\n\nDone check: `python3 -B scripts/test_aa1.py`\n\n" % sub
            self.put("docs/plan/specs/AA1.md", text)
        data = {"as_of": "2026-10-14", "rule": PREAMBLE, "batches": batches}
        data.update(extra)
        with open(self.assign, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def run_main(self, argv, stdin=""):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = PB.main(argv + ["--root", self.root], stdin=io.StringIO(stdin))
        return code, out.getvalue()

    def briefs(self, extra=None):
        return self.run_main(["--assignments", self.assign, "--out", self.out] + (extra or []))

    def listing(self):
        out = []
        for base, _, files in os.walk(self.root):
            out += [os.path.relpath(os.path.join(base, n), self.root) for n in files]
        return sorted(out)


def message(body="DONE: AA1.a 9", tag="REPORT"):
    return "I wrote the files.\n\n```%s\n%s\n```\n\nThat is all." % (tag, body)


class TestWidth(Fix, unittest.TestCase):
    def test_width_is_capped_by_the_env_and_defaults_to_four(self):
        self.assertEqual(PB.width(10, {}), 4)
        self.assertEqual(PB.width(2, {}), 2)
        self.assertEqual(PB.width(0, {}), 0)
        self.assertEqual(PB.width(46, {PB.WIDTH_ENV: "46"}), 46)
        self.assertEqual(PB.width(46, {PB.WIDTH_ENV: "3"}), 3)
        self.assertEqual(PB.width(2, {PB.WIDTH_ENV: "9"}), 2)
        os.environ[PB.WIDTH_ENV] = "6"  # no env argument: the process environment answers
        self.assertEqual(PB.width(10), 6)

    def test_a_width_that_cannot_be_read_is_a_refusal_never_unlimited(self):
        for bad in ("abc", "0", "-1", "", " 4", "4 ", "4.5", "1e3", "0x4", "9999999", chr(0x664)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PB.width(10, {PB.WIDTH_ENV: bad})
        for bad in (-1, True, 2.5, "3", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PB.width(bad, {})
        self.write_batches([batch("AA1.a")])
        os.environ[PB.WIDTH_ENV] = "abc"
        code, out = self.briefs()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.listing(), ["docs/plan/specs/1.0.0/WRITER-ASSIGNMENTS.json", "docs/plan/specs/AA1.md"])


class TestBriefs(Fix, unittest.TestCase):
    def test_every_brief_carries_the_self_score_and_report_rules(self):
        cached = [{"path": "scripts/code.py", "line": 5, "text": "def helper():\n    pass"}]
        text = PB.brief_text(batch("AA1.a", cached_reads=cached), PREAMBLE, SPEC)
        for needle in (PREAMBLE, "spec_score.py --no-claim AA1", "Exit 0", "Stop after three rounds", "%d bytes" % PB.CHUNK_BYTES,
                       "15h20", "ONE fenced block", PB.REPORT_FENCE, "docs/plan/specs/1.0.0/REPORT-AA1.a.md", "HELD:", "DONE:",
                       "scripts/code.py line 5", "def helper():", "### AA1.a first sub unit",
                       "Done check: `python3 -B scripts/test_aa1.py`", "#### Edges and failures"):
            self.assertIn(needle, text)
        self.assertNotIn("second sub unit", text)  # only this batch's section, never a sibling's
        self.assertNotIn("a sibling whose id starts the same", text)  # AA1.a does not pull in AA1.a1
        self.assertNotIn("close text", text)

    def test_a_sub_unit_id_that_starts_another_ones_does_not_match_it(self):
        spec = "### AA1.a1 longer id\n\nLONGER\n\n### AA1.a short id\n\nSHORT\n"
        self.assertEqual(PB.section_of(spec, "AA1.a"), "### AA1.a short id\n\nSHORT\n")
        self.assertEqual(PB.section_of(spec, "AA1.a1"), "### AA1.a1 longer id\n\nLONGER\n")
        self.assertIsNone(PB.section_of(spec, "AA1"))
        self.assertIsNone(PB.section_of("", "AA1.a"))

    def test_a_multi_unit_batch_scores_every_unit_and_pastes_every_section(self):
        text = PB.brief_text(batch("W1", subs=["AA1.a", "AA1.b"], units=["AA1", "BB2"]), PREAMBLE, SPEC)
        self.assertIn("spec_score.py --no-claim AA1 BB2", text)
        self.assertIn("### AA1.a first sub unit", text)
        self.assertIn("### AA1.b second sub unit", text)

    def test_a_batch_with_no_section_refuses(self):
        with self.assertRaisesRegex(ValueError, "no section in the spec for sub unit AA1.zz"):
            PB.brief_text(batch("AA1.zz"), PREAMBLE, SPEC)
        self.write_batches([batch("AA1.a"), batch("AA1.zz")], fill=False)
        code, out = self.briefs()
        self.assertEqual(code, 1, out)
        self.assertIn("AA1.zz", out)
        self.assertFalse(os.path.exists(self.out), "a refusal writes no brief at all, not even the good one")

    def test_a_malformed_batch_or_preamble_is_refused_by_name(self):
        for bad in (None, [], "text", {}, batch("a/b"), batch(".hidden"), batch(""), dict(batch("AA1.a"), sub_units=[]),
                    dict(batch("AA1.a"), sub_units="AA1.a"), dict(batch("AA1.a"), units=[]), dict(batch("AA1.a"), units=[3])):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PB.brief_text(bad, PREAMBLE, SPEC)
        for pre in ("", "   ", None, 7):
            with self.assertRaises(ValueError, msg=repr(pre)):
                PB.brief_text(batch("AA1.a"), pre, SPEC)
        with self.assertRaises(ValueError):
            PB.brief_text(batch("AA1.a"), PREAMBLE, None)

    def test_briefs_are_written_one_per_batch_and_regenerated_with_the_new_section(self):
        self.write_batches([batch("AA1.a"), batch("AA1.b")])
        code, out = self.briefs()
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(os.listdir(self.out)), ["AA1.a.md", "AA1.b.md"])
        self.put("docs/plan/specs/AA1.md", SPEC.replace("first sub unit", "first sub unit REWRITTEN"))
        self.assertEqual(self.briefs()[0], 0)
        with open(os.path.join(self.out, "AA1.a.md"), encoding="utf-8") as f:
            self.assertIn("REWRITTEN", f.read())

    def test_the_preamble_comes_from_a_file_the_batch_file_names_or_a_flag(self):
        self.put("docs/plan/PRE.md", "FILE PREAMBLE")
        self.write_batches([batch("AA1.a")], preamble="docs/plan/PRE.md")
        self.assertEqual(self.briefs()[0], 0)
        with open(os.path.join(self.out, "AA1.a.md"), encoding="utf-8") as f:
            self.assertIn("FILE PREAMBLE", f.read())
        flag = self.put("FLAG.md", "FLAG PREAMBLE")
        self.assertEqual(self.briefs(["--preamble", flag])[0], 0)
        with open(os.path.join(self.out, "AA1.a.md"), encoding="utf-8") as f:
            self.assertIn("FLAG PREAMBLE", f.read())
        self.write_batches([batch("AA1.a")], rule="")  # no preamble at all: nothing to give a writer
        self.assertEqual(self.briefs()[0], 1)


class TestWaves(Fix, unittest.TestCase):
    def test_waves_are_width_wide_longest_first_with_the_arithmetic(self):
        bs = [batch("S1"), batch("L1", subs=["AA1.a", "AA1.b", "AA1.c"]), batch("S2"), batch("M1", subs=["AA1.a", "AA1.b"]), batch("S3")]
        table = PB.waves(bs, 2)
        self.assertEqual([[b["id"] for b in w] for w in table], [["L1", "M1"], ["S1", "S2"], ["S3"]])
        self.assertEqual([[b["id"] for b in w] for w in PB.waves(bs, 9)], [["L1", "M1", "S1", "S2", "S3"]])
        self.assertEqual(PB.waves([], 4), [])
        for bad in (0, -1, True, 2.0, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PB.waves(bs, bad)
        self.write_batches(bs)
        os.environ[PB.WIDTH_ENV] = "2"
        code, out = self.briefs()
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "WAVES: 3 width 2 (limit UNVERIFIED)")
        self.assertEqual(lines[1], "WAVE 1: L1, M1 (about 190 s per wave, W2, 2026-10-14)")
        self.assertEqual(lines[3], "WAVE 3: S3 (about 190 s per wave, W2, 2026-10-14)")
        self.assertEqual(lines[4], "ARITHMETIC: ceil(5 / 2) = 3 waves x about 190 s = about 570 s")

    def test_forty_six_batches_are_twelve_waves_at_four_and_one_wave_at_full_width(self):
        bs = [batch("B%02d" % i) for i in range(46)]
        self.write_batches(bs)
        self.assertEqual(self.briefs()[1].splitlines()[0], "WAVES: 12 width 4 (limit UNVERIFIED)")
        os.environ[PB.WIDTH_ENV] = "46"
        self.assertEqual(self.briefs()[1].splitlines()[0], "WAVES: 1 width 46 (limit UNVERIFIED)")

    def test_zero_batches_is_nothing_to_dispatch_not_a_pass(self):
        self.write_batches([])
        code, out = self.briefs()
        self.assertEqual(code, 2, out)
        self.assertIn("WAVES: 0", out)
        self.assertFalse(os.path.exists(self.out))

    def test_exactly_one_batch_is_one_brief_in_one_wave(self):
        self.write_batches([batch("AA1.a")])
        code, out = self.briefs()
        self.assertEqual((code, out.splitlines()[0]), (0, "WAVES: 1 width 1 (limit UNVERIFIED)"))


class TestReports(Fix, unittest.TestCase):
    def test_a_report_is_filed_from_one_fenced_block_only(self):
        self.assertEqual(PB.extract_report(message("DONE: AA1.a 9\nmore")), "DONE: AA1.a 9\nmore\n")
        for bad, why in (("I wrote the REPORT, it is long, no fence at all", "no fenced"),
                         (message(tag="python"), "no fenced"),
                         (message(tag="report"), "no fenced"),  # the tag is exact
                         ("```REPORT\nopened and never closed", "never closed"),
                         (message() + "\n" + message("second"), "2 fenced"),
                         ("```REPORT\nfirst\n```REPORT\ninside\n```", "2 fenced"),
                         (message(""), "empty"), (message("   "), "empty"),
                         (message("x" * (PB.MAX_REPORT_BYTES + 1)), "over")):
            with self.assertRaisesRegex(ValueError, why, msg=bad[:40]):
                PB.extract_report(bad)
        for not_text in (None, 7, b"```REPORT\nx\n```"):
            with self.assertRaises(ValueError):
                PB.extract_report(not_text)

    def test_a_huge_message_without_a_block_refuses_quickly(self):
        with self.assertRaises(ValueError):
            PB.extract_report("REPORT word in prose\n" * 250000)

    def test_only_the_block_text_is_filed_not_the_message_around_it(self):
        path = os.path.join(self.tmp, "r.md")
        PB.file_report(message("DONE: AA1.a 9"), path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "DONE: AA1.a 9\n")

    def test_filing_never_overwrites_a_different_report(self):
        path = os.path.join(self.tmp, "reports", "r.md")
        self.assertEqual(PB.file_report(message("first"), path), path)
        self.assertEqual(PB.file_report(message("first"), path), path)  # the same bytes again pass
        with self.assertRaisesRegex(ValueError, "different report"):
            PB.file_report(message("second"), path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "first\n")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["r.md"])  # no temporary file left behind
        with self.assertRaises(ValueError):
            PB.file_report("no block", os.path.join(self.tmp, "reports", "none.md"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "reports", "none.md")), "an unextractable report is never filed empty")

    def test_the_entry_point_files_a_report_to_the_batch_report_path(self):
        self.write_batches([batch("AA1.a"), batch("AA1.b")])
        code, out = self.run_main(["--assignments", self.assign, "--file-report", "AA1.a"], stdin=message("HELD: misses"))
        self.assertEqual(code, 0, out)
        target = os.path.join(self.root, "docs/plan/specs/1.0.0/REPORT-AA1.a.md")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "HELD: misses\n")
        self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", "AA1.a"], stdin=message("HELD: misses"))[0], 0)
        self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", "AA1.a"], stdin=message("other"))[0], 1)
        self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", "AA1.a"], stdin="no block")[0], 1)
        self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", "NOPE"], stdin=message())[0], 1)
        self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", "AA1.b"], stdin="")[0], 1)

    def test_a_report_path_leaving_the_root_or_a_missing_one_is_refused(self):
        self.write_batches([batch("AA1.a", report="../escape.md"), batch("AA1.b", report=None)])
        for sub in ("AA1.a", "AA1.b"):
            self.assertEqual(self.run_main(["--assignments", self.assign, "--file-report", sub], stdin=message())[0], 1, sub)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.md")))


class TestEntryPoint(Fix, unittest.TestCase):
    def test_a_missing_or_corrupt_batch_file_is_no_data_or_a_refusal(self):
        self.assertEqual(self.briefs()[0], 2)  # nothing written yet: NO-DATA
        for bad in ("{not json", "[1]", json.dumps({"batches": "x"}), json.dumps({"batches": [1]})):
            with open(self.assign, "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertEqual(self.briefs()[0], 1, bad)

    def test_hostile_batch_ids_and_duplicates_write_nothing(self):
        for bad in ([batch("a/b")], [batch("../x")], [batch("AA1.a"), batch("AA1.a")], [batch("")]):
            self.write_batches(bad)
            self.assertEqual(self.briefs()[0], 1, repr([b["id"] for b in bad]))
            self.assertFalse(os.path.exists(self.out))

    def test_a_spec_path_leaving_the_root_or_gone_is_refused(self):
        self.write_batches([batch("AA1.a", section={"file": "../x.md", "sub": "AA1.a"})])
        self.assertEqual(self.briefs()[0], 1)
        self.write_batches([batch("AA1.a", section={"file": "docs/plan/specs/GONE.md", "sub": "AA1.a"})])
        self.assertEqual(self.briefs()[0], 2)
        self.write_batches([dict(batch("AA1.a"), section=None, units=[])])
        self.assertEqual(self.briefs()[0], 1)

    def test_a_batch_without_a_section_key_reads_the_spec_of_its_units(self):
        b = batch("AA1.a")
        del b["section"]
        self.write_batches([b])
        self.assertEqual(self.briefs()[0], 0)

    def test_the_text_never_holds_a_dash_or_clock_text(self):
        self.write_batches([batch("AA1.a")])
        self.briefs()
        with open(os.path.join(self.out, "AA1.a.md"), encoding="utf-8") as f:
            text = f.read()
        for bad in (chr(0x2013), chr(0x2014), CLOCK):
            self.assertNotIn(bad, text)

    @unittest.skipUnless(HUB, "docs/plan/specs is hub only")
    def test_the_real_1_1_4_batch_file_makes_a_brief_for_every_batch(self):
        real = os.path.join(ROOT, "docs", "plan", "specs", "1.1.4", "WRITER-ASSIGNMENTS.json")
        if not os.path.isfile(real):
            self.skipTest("the 1.1.4 batch file is not in this tree")
        with open(real, encoding="utf-8") as f:
            n = len(json.load(f)["batches"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = PB.main(["--assignments", real, "--out", os.path.join(self.tmp, "briefs"), "--root", ROOT], stdin=io.StringIO(""))
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "briefs"))), n)
        self.assertIn("WAVES: ", out.getvalue())


if __name__ == "__main__":
    unittest.main()
