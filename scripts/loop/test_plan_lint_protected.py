#!/usr/bin/env python3
"""LL1.a: PROTECTED-TOUCH. A loop built section that names a path the landing refuses, or regenerates itself, is
BLOCKING before a worker starts. Entry points under test: plan_lint.admission and plan_lint.lint_plan (where the gate
lives), with the helpers checked beside them. Every fixture lives in a tempfile directory; no HOME file is read.

Run: python3 -B scripts/loop/test_plan_lint_protected.py
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import plan_lint  # noqa: E402  (the module under test, beside this file)
import grade_build  # noqa: E402  (the adapter's own rule: the parity oracle)
import runner_pool  # noqa: E402  (write_set: the pool's own reader, the other parity oracle)

BATTERY = "scripts/check_all.sh"


def spec_text(files="`scripts/x.py` (existing), `scripts/test_x.py` (NEW)", built="Built by: Brother.Loop",
              extra="", sub="U.1"):
    """One spec with one section. `built` is the whole Built by line ('' omits it)."""
    if "scripts/test_x.py" not in files:
        files += ", `scripts/test_x.py` (NEW)"   # the section's own test, so TEST-FOUND stays silent
    parts = ["# U", "", "## %s do a thing" % sub, "", "Files: %s" % files, ""]
    if built:
        parts += [built, ""]
    if extra:
        parts += [extra, ""]
    parts += ["Done check: `python3 -B scripts/test_x.py`", ""]
    return "\n".join(parts)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ll1a_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "docs", "plan", "specs"))
        os.makedirs(os.path.join(self.root, "scripts"))
        with open(os.path.join(self.root, "scripts", "x.py"), "w", encoding="utf-8") as fh:
            fh.write("X = 1\n")
        self.unit = {"id": "U", "state": "OPEN", "done_check": "python3 -B scripts/test_x.py",
                     "spec": "docs/plan/specs/U.md", "sub_units": ["U.1"], "evidence": "",
                     "owns": ["scripts/x.py", "scripts/test_x.py"]}   # no owns would be an OWNS BLOCKING (2026-10-14)
        self.ledger = os.path.join(self.root, "absent-ledger.jsonl")   # absent: the prior, never a HOME file

    def put(self, text):
        with open(os.path.join(self.root, "docs", "plan", "specs", "U.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def admit(self, text):
        self.put(text)
        return plan_lint.admission({"units": [self.unit]}, self.unit, "U.1", self.root, {}, ledger=self.ledger)

    def protected(self, text):
        lines, why = self.admit(text)
        return [l for l in lines if "PROTECTED-TOUCH" in l], why


class TestLL1A(Fixture):
    def test_a_loop_built_files_line_naming_check_all_is_refused_with_the_landing_remedy(self):
        self.assertEqual(self.admit(spec_text()), ([], ""))   # the clean twin: every other rule is silent
        hits, why = self.protected(spec_text(files="`scripts/x.py` (existing), `%s` (existing)" % BATTERY))
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("LINT BLOCKING U U.1 PROTECTED-TOUCH: "), hits[0])
        self.assertIn(BATTERY, hits[0])
        self.assertIn("land_batch.register", hits[0])
        self.assertIn("scripts/check_all.sh is a path the landing trusts, which a loop build may not write", hits[0])
        self.assertTrue(why.startswith("LINT PROTECTED-TOUCH: " + BATTERY), why)

    def test_bundle_system_md_and_each_protected_list_member_are_refused(self):
        for path in ("bundle/runtime/loop/plan_lint.py", "Bundle/skills/using-brother/SKILL.md", "SYSTEM.md", "system.md",
                     BATTERY, "scripts/Check_All.sh", "./scripts/check_all.sh",
                     "scripts//check_all.sh", "docs/plan/specs/U.md", ".claude/hooks/x.py", ".github/x.py"):
            with self.subTest(path=path):
                hits, _ = self.protected(spec_text(files="`scripts/x.py`, `%s`" % path))
                self.assertEqual(len(hits), 1, (path, hits))
                self.assertIn(os.path.normpath(path), hits[0])
        self.assertNotEqual(plan_lint.protected_reason("products/brothersbe/CHECKSUMS.sha256"), "")   # not a spec path shape
        for path in sorted(grade_build.PROTECTED_FILES):
            if not path.endswith((".py", ".sh", ".json", ".md")):
                continue
            with self.subTest(grader_member=path):
                self.assertNotEqual(plan_lint.protected_reason(path), "")
        for path in ("scripts/x.py", "scripts/test_x.py", "products/brothersbe/scripts/checksums.sh",
                     "docs/plan/examples/CHECKSUMS-DRY-RUN-RECORD.md", "scripts/loop/plan_lint_notes.md"):
            if path in grade_build.PROTECTED_FILES:
                continue
            with self.subTest(clean=path):
                self.assertEqual(plan_lint.protected_reason(path), "")

    def test_a_session_built_section_may_name_a_protected_path(self):
        files = "`scripts/x.py`, `%s`, `bundle/runtime/loop/x.py`" % BATTERY
        for built in ("Built by: a session builder (effort high) in its own worktree", "Built by: the session, direct.",
                      "Built by: Opus, then the session integrates"):
            with self.subTest(built=built):
                hits, _ = self.protected(spec_text(files=files, built=built))
                self.assertEqual(hits, [])
        self.assertFalse(plan_lint.built_by_loop("Built by: the session"))
        self.assertTrue(plan_lint.built_by_loop("Built by: Brother.Loop (Claude seats)"))
        self.assertTrue(plan_lint.built_by_loop("built by: brother.loop"))   # control: case does not matter

    def test_a_reads_line_and_a_read_only_annotation_do_not_trip_it(self):
        reads = "Reads: `%s` (the registration row to mirror), and update the notes in `bundle/x.py`" % BATTERY
        hits, _ = self.protected(spec_text(extra=reads))
        self.assertEqual(hits, [])
        hits, _ = self.protected(spec_text(files="`scripts/x.py` (existing), `%s` (read only), `SYSTEM.md` (unchanged)"
                                                  % BATTERY))
        self.assertEqual(hits, [])
        hits, _ = self.protected(spec_text(files="`scripts/x.py`, `%s`" % BATTERY))   # control: no annotation trips it
        self.assertEqual(len(hits), 1)

    def test_a_prose_edit_verb_trips_it_and_a_negated_mention_does_not(self):
        for sentence in ("The build registers the new test in `%s`." % BATTERY,
                         "Append one row to `%s` for the test." % BATTERY,
                         "It must also regenerate `bundle/runtime/loop/plan_lint.py`."):
            with self.subTest(sentence=sentence):
                hits, _ = self.protected(spec_text(extra=sentence))
                self.assertEqual(len(hits), 1, hits)
        for sentence in ("The build never edits `%s`." % BATTERY,
                         "Do not add a row to `%s`." % BATTERY,
                         "Write the test without touching `%s`." % BATTERY,
                         "It adds a test. The file `%s` is the landing's own." % BATTERY,   # verb is in another sentence
                         "See `%s`." % BATTERY):                                           # no verb at all
            with self.subTest(sentence=sentence):
                hits, _ = self.protected(spec_text(extra=sentence))
                self.assertEqual(hits, [], sentence)
        self.assertEqual(plan_lint.prose_touch_paths("Update `scripts/y.py` and write `scripts/z.py`."),
                         ["scripts/y.py", "scripts/z.py"])
        self.assertEqual(plan_lint.prose_touch_paths("x" * 70 + " change `scripts/far.py`"), ["scripts/far.py"])
        self.assertEqual(plan_lint.prose_touch_paths("change " + "x" * 70 + " `scripts/far.py`"), [])   # beyond 60 characters

    def test_a_section_with_no_built_by_line_is_treated_as_loop_built(self):
        files = "`scripts/x.py`, `%s`" % BATTERY
        hits, _ = self.protected(spec_text(files=files, built=""))
        self.assertEqual(len(hits), 1, hits)
        hits, _ = self.protected(spec_text(files=files, built="Built by: a contractor nobody has heard of"))
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(plan_lint.built_by_loop("Built by: Brother.Loop, then the session integrates"))   # both: loop wins
        self.assertTrue(plan_lint.built_by_loop(""))
        self.assertTrue(plan_lint.built_by_loop(None))
        self.assertTrue(plan_lint.built_by_loop(17))

    def test_protected_reason_agrees_with_the_graders_dest(self):
        paths = ["scripts/x.py", "scripts/check_all.sh", "Scripts/CHECK_ALL.sh", "scripts/loop/grade_build.py",
                 "scripts/loop/plan_lint.py", ".github/workflows/ci.py", ".claude/x.py", "docs/plan/specs/A.md",
                 "docs/plan/BROTHER-1.1.4-WBS.json", "a/.gitignore", "scripts/loop/sandbox.sb", "scripts/loop/plan_path.py",
                 "../escape.py", "/abs/x.py", "a/.git/x.py", "scripts/loop/__init__.py", "scripts/json.py", "os.py",
                 "sitecustomize.py", "docs/notes.md"] + list(grade_build.PROTECTED_FILES)
        for path in paths:
            if path.casefold().startswith(plan_lint.PROTECTED_EXTRA):
                continue
            full, _ = grade_build.dest(self.root, path)
            with self.subTest(path=path):
                self.assertEqual(bool(plan_lint.protected_reason(path)), full is None)
        # the extras are the part dest does NOT know: control that they are refused here and only here
        for path in ("bundle/runtime/loop/x.py", "SYSTEM.md"):
            self.assertIsNotNone(grade_build.dest(self.root, path)[0])
            self.assertNotEqual(plan_lint.protected_reason(path), "")
        self.assertEqual(plan_lint.protected_reason("../x.py"), "a path the grader refuses (path escapes the tree)")

    def test_write_paths_agrees_with_the_pools_write_set(self):
        spec = "\n".join([
            "# U", "", "## U.1 one", "",
            "Files: `scripts/a.py` (existing), `./scripts/b.py` (NEW), `scripts/c.py` (read only),",
            "`scripts/d.py` (unchanged), `bundle/gen.py` (existing)", "",
            "Reads: `scripts/e.py`", "", "## U.2 two", "",
            "Owns: `scripts/f.py`, `scripts/g.py` (existing) Reads: `scripts/h.py`", "",
            "## U.3 three", "", "Prose only about `scripts/i.py`.", "",
            "## U.4 four", "", "Files: `scripts/j.py`.", "", "Owns: `scripts/k.py` (NEW)", ""])
        path = os.path.join(self.root, "docs", "plan", "specs", "P.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(spec)
        runs = os.path.join(self.root, "runs")
        os.makedirs(runs)
        for sub in ("U.1", "U.2", "U.3", "U.4"):
            with self.subTest(sub=sub):
                mine = plan_lint.write_paths(plan_lint._section(spec, sub))
                theirs = runner_pool.write_set(sub, path, runs_dir=runs)
                kept = {p for p in mine if not any(p == g or p.startswith(g) for g in runner_pool.GENERATED)}
                if theirs is None:
                    self.assertEqual(mine, [])
                else:
                    self.assertEqual(kept, theirs)
        self.assertEqual(plan_lint.write_paths(plan_lint._section(spec, "U.1")),
                         ["scripts/a.py", "scripts/b.py", "bundle/gen.py"])

    def test_lint_plan_reports_the_finding_blocking_and_admission_returns_it_as_why(self):
        self.put(spec_text(files="`scripts/x.py`, `%s`" % BATTERY))
        plan = os.path.join(self.root, "plan.json")
        with open(plan, "w", encoding="utf-8") as fh:
            json.dump({"units": [self.unit]}, fh)
        report = plan_lint.lint_plan(plan, root=self.root, ledger=None, env={})
        hits = [f for f in report["findings"] if f["rule"] == "PROTECTED-TOUCH"]
        self.assertEqual([(f["unit"], f["sub"], f["severity"]) for f in hits], [("U", "U.1", "BLOCKING")])
        self.assertIn(BATTERY, hits[0]["text"])
        lines, why = plan_lint.admission({"units": [self.unit]}, self.unit, "U.1", self.root, {}, ledger=self.ledger)
        self.assertTrue(why.startswith("LINT PROTECTED-TOUCH: "), why)
        self.assertEqual(len([l for l in lines if "PROTECTED-TOUCH" in l]), 1)
        # the report CLI prints it and counts it in the summary
        import contextlib
        import io
        out = io.StringIO()
        here = os.getcwd()
        os.chdir(self.root)   # main() reads the repository root as the working directory
        try:
            with contextlib.redirect_stdout(out):
                plan_lint.main(["--report", plan, "--ledger", self.ledger])
        finally:
            os.chdir(here)
        self.assertRegex(out.getvalue(), r"LINT BLOCKING U U\.1 PROTECTED-TOUCH: ")

    def test_hostile_inputs_return_no_data_never_a_pass(self):
        good = spec_text(files="`scripts/x.py`, `%s`" % BATTERY)
        for unit in (None, "U", ["U"], 17):
            with self.subTest(unit=unit):
                got = plan_lint.hard_findings("U", "U.1", unit, good)
                self.assertEqual([(f["rule"], f["severity"]) for f in got], [("PROTECTED-TOUCH", "NO-DATA")])
        for text in (None, 17, b"bytes", ["x"]):
            with self.subTest(text=text):
                got = plan_lint.hard_findings("U", "U.1", self.unit, text)
                self.assertEqual([f["severity"] for f in got], ["NO-DATA"])
        for uid, sub in ((None, "U.1"), ("U", None), (17, 17)):
            got = plan_lint.hard_findings(uid, sub, None, good)
            self.assertEqual([f["severity"] for f in got], ["NO-DATA"])
        # empty, truncated and absent sections: no finding from this rule (SECTION-CHECK owns a missing one)
        self.assertEqual(plan_lint.hard_findings("U", "U.1", self.unit, ""), [])
        self.assertEqual(plan_lint.hard_findings("U", "U.9", self.unit, good), [])
        self.assertEqual(plan_lint.hard_findings("U", "U.1", self.unit, "## U.1 cut off\n\nFiles:"), [])
        # a repeated path is one finding; a NUL in a token is not a path
        owner = dict(self.unit, owns=self.unit["owns"] + [BATTERY])   # owns the path, so only PROTECTED-TOUCH can answer
        twice = spec_text(files="`%s`, `%s`" % (BATTERY, BATTERY))
        self.assertEqual(len(plan_lint.hard_findings("U", "U.1", owner, twice)), 1)
        both = spec_text(files="`%s`" % BATTERY, extra="Register the test in `%s`." % BATTERY)   # Files line and prose
        self.assertEqual(len(plan_lint.hard_findings("U", "U.1", owner, both)), 1)
        self.assertEqual(plan_lint.write_paths(None), [])
        self.assertEqual(plan_lint.prose_touch_paths(None), [])
        self.assertEqual(plan_lint.write_paths("Files: `scripts/x\0.py`"), [])
        for bad in (None, "", 17, b"x", "a\0b"):
            self.assertNotEqual(plan_lint.protected_reason(bad), "", repr(bad))
        # the same call twice gives the same answer (no state between calls)
        self.assertEqual(plan_lint.hard_findings("U", "U.1", self.unit, good), plan_lint.hard_findings("U", "U.1", self.unit, good))


if __name__ == "__main__":
    unittest.main()
