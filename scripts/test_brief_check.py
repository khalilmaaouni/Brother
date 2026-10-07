#!/usr/bin/env python3
"""brief_check refuses a brief on each of its five properties, one fixture per property, and its entry point is the
thing under test (a control tested at a helper it calls is not tested). Owner order 2026-09-24: briefs per unit and
from the epic, ENFORCED. Fixtures are built in a temp tree, never borrowed from this repository."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("brief_check", os.path.join(HERE, "loop", "brief_check.py"))
brief_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brief_check)

WBS = ("THIS UNIT, FROM THE WBS (docs/plan/WBS.json):\n  initiative: the launch\n  unit X: a unit\n"
       "  objective: do the thing\n  closes when: python3 -B -m unittest pkg.test_thing\n  checker: tests\n")
SPEC = "# X\n\n### X.1 the sub unit\nFile: `pkg/test_thing.py` existing.\nDone check: `python3 -B -m unittest pkg.test_thing`\n\n### X.2 other\nnothing\n"


class TheGate(unittest.TestCase):
    # H4.a round 3. The red team's 24 probes build their valid baseline FROM THIS CASE and read the fixture off
    # it (self.SPEC, self.WBS). As module globals only, every one of them died with AttributeError
    # "'TheGate' object has no attribute 'SPEC'" before it could ask this module anything at all. Reachable on
    # the case AND at module level, so a probe with or without setUp gets the same fixture.
    SPEC = SPEC
    WBS = WBS

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="brief-check-")
        for d in ("pkg", "scripts"):
            os.makedirs(os.path.join(self.tmp, d))
        self.write("pkg/test_thing.py", "import os\nimport helper\n")
        self.write("scripts/helper.py", "x = 1\n")
        self.write("spec.md", SPEC)
        self._cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self._cwd)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, text):
        with open(os.path.join(self.tmp, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
        return rel

    def brief(self, wbs=WBS, test_shown=True, helper_shown=True, pad=0):
        b = "House laws.\n\n" + wbs + "\n" + "THE WHOLE UNIT SPECIFICATION:\n" + SPEC + "\n\nTHE REAL FILES:\n"
        b += "===== FILE: pkg/test_thing.py =====\n" + ("import os\nimport helper\n" if test_shown else "[NOT SHOWN: too big]\n")
        b += "===== FILE: scripts/helper.py =====\n" + ("x = 1\n" if helper_shown else "[NOT SHOWN: private term]\n")
        b += "x" * pad
        return self.write("brief.md", b)

    def run_main(self, brief_path, sub="X.1", spec="spec.md"):
        out = io.StringIO()
        with redirect_stdout(out):
            code = brief_check.main(["X", sub, spec, brief_path])
        return code, out.getvalue()

    def test_a_complete_brief_passes_at_the_entry_point(self):
        code, out = self.run_main(self.brief())
        self.assertEqual(code, 0, out)
        self.assertIn("BRIEF X X.1: PASS", out)
        self.assertEqual(out.count("PASS"), 6, out)      # five properties and the verdict line

    def test_B1_a_section_named_file_not_shown_is_refused(self):
        code, out = self.run_main(self.brief(test_shown=False))
        self.assertEqual(code, 1)
        self.assertIn("B1 NAMED SHOWN    REFUSED", out)
        self.assertIn("pkg/test_thing.py", out)
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)     # only B1 tripped: the fixture isolates one condition

    def test_B2_a_module_the_named_test_imports_not_shown_is_refused(self):
        code, out = self.run_main(self.brief(helper_shown=False))
        self.assertEqual(code, 1)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("scripts/helper.py", out)
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B3_a_brief_over_the_dispatcher_budget_is_refused(self):
        code, out = self.run_main(self.brief(pad=brief_check.BUDGET))
        self.assertEqual(code, 1)
        self.assertIn("B3 BUDGET         REFUSED", out)
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B4_a_section_with_no_runnable_done_check_is_refused(self):
        self.write("spec.md", SPEC.replace("`python3 -B -m unittest pkg.test_thing`", "`python3 -B -m unittest pkg.test_thing && python3 -B scripts/test_x.py`"))
        code, out = self.run_main(self.brief())
        self.assertEqual(code, 1)
        self.assertIn("B4 DONE CHECK     REFUSED", out)
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B4_a_fenced_block_the_landing_gate_chains_is_refused(self):
        # 2026-09-29: every line of the block matched the grader's regex, so B4 passed, while the landing gate joins the
        # block with && and refuses it. B4 is now the gate's own verdict (spec_check.gate_refusal).
        self.write("spec.md", SPEC.replace("`python3 -B -m unittest pkg.test_thing`",
                                           "\n\n```\npython3 -B -m unittest pkg.test_thing\npython3 -B scripts/test_x.py\n```"))
        code, out = self.run_main(self.brief())
        self.assertEqual(code, 1)
        self.assertIn("B4 DONE CHECK     REFUSED", out)
        self.assertIn("landing gate", out)
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B4_a_fenced_block_of_one_accepted_command_passes(self):
        self.write("spec.md", SPEC.replace("`python3 -B -m unittest pkg.test_thing`", "\n\n```\npython3 -B -m unittest pkg.test_thing\n```"))
        code, out = self.run_main(self.brief())
        self.assertIn("B4 DONE CHECK     PASS", out)

    def test_B5_a_brief_that_does_not_state_the_wbs_row_is_refused(self):
        code, out = self.run_main(self.brief(wbs="THIS UNIT, FROM THE WBS: NO-DATA (docs/plan/WBS.json: missing)\n"))
        self.assertEqual(code, 1)
        self.assertIn("B5 FROM THE WBS   REFUSED", out)
        self.assertIn("B4 DONE CHECK     PASS", out)

    def test_B5_a_no_data_field_inside_the_row_is_refused(self):
        code, out = self.run_main(self.brief(wbs=WBS.replace("  objective: do the thing", "  objective: NO-DATA")))
        self.assertEqual(code, 1)
        self.assertIn("B5 FROM THE WBS   REFUSED", out)
        self.assertIn("objective", out)

    def test_an_unreadable_brief_is_NO_DATA_never_a_pass(self):
        code, out = self.run_main("there-is-no-such-brief.md")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_a_missing_section_is_NO_DATA(self):
        code, out = self.run_main(self.brief(), sub="X.9")
        self.assertEqual(code, 2)
        self.assertIn("no section for X.9", out)

    def test_wrong_argv_is_NO_DATA(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(brief_check.main(["X"]), 2)
        self.assertIn("NO-DATA", out.getvalue())


    # H4.a REQ-H-UNREAD: an unreadable named test refuses the brief, never passes it, and every hostile input is
    # refused at its source instead of being answered with the safe empty value. One fixture per finding class.

    def test_B2_a_directory_where_the_named_test_belongs_is_refused(self):
        os.makedirs(os.path.join(self.tmp, "pkg", "test_unreadable.py"))
        spec = SPEC.replace("pkg/test_thing.py", "pkg/test_unreadable.py")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_unreadable.py", "import os\nimport helper\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("pkg/test_unreadable.py", out)
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B2_a_test_the_section_marks_NEW_and_that_does_not_exist_yet_passes(self):
        # 2026-09-25: five sub units (D14.6, H3.c, H4.b, L5a-3, R2.1) were WITHHELD before a single build because the
        # test their spec tells the builder to CREATE did not exist yet and read as unreadable; the pool drained.
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `pkg/test_new.py` NEW.")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_new.py", "(to be written by the builder)\n"))
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)

    def test_B2_NEW_written_before_the_path_passes(self):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: NEW `pkg/test_new.py`.")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_new.py", "(to be written by the builder)\n"))
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)

    def test_B2_NEW_in_parentheses_passes(self):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `pkg/test_new.py` (NEW).")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_new.py", "(to be written by the builder)\n"))
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)

    def test_B2_a_new_test_named_again_by_its_bare_file_name_passes(self):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `pkg/test_new.py` NEW. Every clause is covered in `test_new.py`.")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_new.py", "(to be written by the builder)\n"))
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)

    def test_B2_a_bare_name_that_is_no_new_test_is_still_refused(self):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `pkg/test_new.py` NEW. See also `test_other.py`.")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_new.py", "(to be written by the builder)\n"))
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("test_other.py", out)

    def test_B2_a_named_existing_test_that_is_missing_is_still_refused(self):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `pkg/test_gone.py` existing.")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_gone.py", "import os\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("pkg/test_gone.py", out)

    def test_B2_a_named_test_with_a_syntax_error_is_refused(self):
        self.write("pkg/test_bad.py", "def (\n")
        spec = SPEC.replace("pkg/test_thing.py", "pkg/test_bad.py")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_bad.py", "def (\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("pkg/test_bad.py", out)
        self.assertIn("syntax", out.lower())
        self.assertIn("B1 NAMED SHOWN    PASS", out)

    def test_B2_a_named_test_that_is_not_utf8_is_refused(self):
        with open(os.path.join(self.tmp, "pkg", "test_binary.py"), "wb") as fh:
            fh.write(b"\xff\xfeimport os\n")
        spec = SPEC.replace("pkg/test_thing.py", "pkg/test_binary.py")
        code, out = self.run_main(self.brief_with(spec, "pkg/test_binary.py", "not really text\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
        self.assertIn("utf-8", out)

    def test_named_test_readable_refuses_hostile_input_with_a_reason(self):
        for bad in (None, 123, True, b"pkg/test_thing.py", ["pkg/test_thing.py"]):
            ok, reason = brief_check.named_test_readable(bad)
            self.assertFalse(ok, repr(bad))
            self.assertIn("str", reason)

    def test_named_test_readable_refuses_a_directory_and_a_missing_file(self):
        ok, reason = brief_check.named_test_readable(self.tmp)
        self.assertFalse(ok, reason)
        self.assertTrue(reason)
        ok, reason = brief_check.named_test_readable(os.path.join(self.tmp, "no_such_test.py"))
        self.assertFalse(ok, reason)
        self.assertIn("unreadable", reason)

    def test_a_label_before_or_after_the_path_still_names_it_and_data_files_are_not_source(self):
        section = "`plugin/a/test_x.py (NEW)` and `NEW: scripts/y.py` and `scripts/z.py (existing)` and `logs/g.jsonl`"
        self.assertEqual(brief_check.named_paths(section), ["plugin/a/test_x.py", "scripts/y.py", "scripts/z.py"])

    def test_the_readers_refuse_non_text_instead_of_returning_the_safe_value(self):
        for bad in (None, 123, True, b"text", ["text"], (c for c in "text")):
            with self.assertRaises(ValueError, msg=repr(bad)):
                brief_check.named_paths(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                brief_check.shown_in(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                brief_check.test_imports(bad)

    def test_test_imports_reads_a_readable_file_and_refuses_a_directory(self):
        self.assertEqual(brief_check.test_imports(os.path.join(self.tmp, "pkg", "test_thing.py")), ["scripts/helper.py"])
        with self.assertRaises(ValueError):
            brief_check.test_imports(self.tmp)

    def test_test_imports_refuses_a_non_utf8_file_and_a_missing_one(self):
        path = os.path.join(self.tmp, "pkg", "not_utf8.py")
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfeimport os\n")
        with self.assertRaises(ValueError) as cm:
            brief_check.test_imports(path)
        self.assertIn("utf-8", str(cm.exception))
        with self.assertRaises(ValueError):
            brief_check.test_imports(os.path.join(self.tmp, "pkg", "gone.py"))

    def test_check_refuses_non_text_as_NO_DATA(self):
        for sub, spec, brief in ((None, SPEC, SPEC), ("X.1", None, SPEC), ("X.1", SPEC, None), (b"X.1", SPEC, SPEC)):
            self.assertIsNone(brief_check.check(sub, spec, brief))

    def test_section_of_refuses_non_text_as_NO_DATA(self):
        self.assertIsNone(brief_check.section_of(None, "X.1"))
        self.assertIsNone(brief_check.section_of(SPEC, None))
        self.assertIsNone(brief_check.section_of(None, None))

    def test_main_refuses_an_unknown_flag_never_a_pass(self):
        brief = self.write("brief.md", "House laws.\n\n" + WBS + "\nTHE WHOLE UNIT SPECIFICATION:\n" + SPEC
                           + "\n\nTHE REAL FILES:\n===== FILE: pkg/test_thing.py =====\nimport os\nimport helper\n"
                           + "===== FILE: scripts/helper.py =====\nx = 1\n")
        out = io.StringIO()
        with redirect_stdout(out):
            code = brief_check.main(["--unknown", "X.1", "spec.md", brief])
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("NO-DATA", out.getvalue())
        self.assertIn("--unknown", out.getvalue())

    def test_main_refuses_argv_that_is_not_four_strings(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(brief_check.main(a for a in ("X", "X.1", "spec.md", "brief.md")), 2)
            self.assertEqual(brief_check.main(["X", "X.1", "spec.md", 4]), 2)
            self.assertEqual(brief_check.main("X X.1 spec.md brief.md"), 2)
            self.assertEqual(brief_check.main(["X", "X.1", "spec.md", "brief.md", "--flag"]), 2)
        self.assertIn("NO-DATA", out.getvalue())

    def test_the_case_exposes_the_fixture_the_probes_read(self):
        # The red team's probes build their valid baseline from THIS case and read self.SPEC and self.WBS. As
        # module globals only, all 24 of them died with AttributeError before they could ask brief_check anything.
        self.assertEqual(self.SPEC, SPEC)
        self.assertEqual(self.WBS, WBS)
        self.assertIn("### X.1 the sub unit", self.SPEC)
        self.assertIn("closes when:", self.WBS)
        self.assertEqual(type(self).SPEC, SPEC)      # a class level read, no setUp needed
        self.assertEqual(type(self).WBS, WBS)

    # FX-09.1: THE LABEL MAY SIT INSIDE THE BACKTICKS. docs/plan/specs/L0.md writes `NEW: tests/test_l0_scan.py`; B2 read
    # only the outside spellings and parked L0.2 four times on 2026-09-28 as "unreadable". labelled_paths is the one reader.

    def new_label(self, spelled, present=None):
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: %s." % spelled)
        if present is not None:
            self.write("pkg/test_new.py", present)
        return self.run_main(self.brief_with(spec, "pkg/test_new.py", present or "(to be written by the builder)\n"))

    def test_B2_NEW_label_before_the_path_inside_the_backticks_passes(self):
        code, out = self.new_label("`NEW: pkg/test_new.py`")
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)
        self.assertEqual(code, 0, out)

    def test_B2_NEW_label_after_the_path_inside_the_backticks_passes(self):
        code, out = self.new_label("`pkg/test_new.py (NEW)`")
        self.assertIn("B2 IMPORTS SHOWN  PASS", out)
        self.assertEqual(code, 0, out)

    def test_B2_a_present_test_labelled_NEW_inside_the_backticks_is_still_read(self):
        for spelled in ("`NEW: pkg/test_new.py`", "`pkg/test_new.py (NEW)`"):
            code, out = self.new_label(spelled, present="def (\n")
            self.assertEqual(code, 1, out)
            self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
            self.assertIn("syntax", out.lower())

    def test_B2_an_existing_label_is_no_exemption(self):
        for spelled in ("`existing: pkg/test_new.py`", "`pkg/test_new.py (existing)`"):
            code, out = self.new_label(spelled)
            self.assertEqual(code, 1, out)
            self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)
            self.assertIn("pkg/test_new.py (unreadable", out)

    def test_B2_a_lowercase_new_label_is_no_exemption(self):
        for spelled in ("`new: pkg/test_new.py`", "`Reads: pkg/test_new.py`"):
            code, out = self.new_label(spelled)
            self.assertEqual(code, 1, (spelled, out))
            self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)

    def test_B2_uses_labelled_paths(self):
        from unittest import mock
        with mock.patch.object(brief_check, "labelled_paths", lambda text: []):
            code, out = self.new_label("`NEW: pkg/test_new.py`")
        self.assertEqual(code, 1, out)
        self.assertIn("B2 IMPORTS SHOWN  REFUSED", out)

    def test_B2_one_new_test_present_one_absent(self):
        self.write("pkg/test_a.py", "import helper\n")
        spec = SPEC.replace("File: `pkg/test_thing.py` existing.", "Tests: `NEW: pkg/test_a.py` and `pkg/test_b.py (NEW)`.")
        self.write("spec.md", spec)
        brief = ("House laws.\n\n" + WBS + "\nTHE WHOLE UNIT SPECIFICATION:\n" + spec + "\n\nTHE REAL FILES:\n"
                 "===== FILE: pkg/test_a.py =====\nimport helper\n===== FILE: scripts/helper.py =====\n[NOT SHOWN: cut]\n")
        code, out = self.run_main(self.write("brief.md", brief))
        self.assertEqual(code, 1, out)                  # the PRESENT new test is read, and its import is not shown
        self.assertIn("B2 IMPORTS SHOWN  REFUSED: module(s) a named test imports not shown: scripts/helper.py", out)
        self.assertNotIn("test_b.py", out)              # the absent one is exempt: it is labelled NEW

    def test_labelled_paths_reads_every_spelling_in_order_with_duplicates(self):
        text = ("`NEW: a/test_1.py` `b/test_2.py (NEW)` `c/test_3.py` NEW, NEW `d/test_4.py` `c/test_3.py` "
                "`existing: e/f.py` `g/h.py (existing)` `new: i/j.py` `Reads: k/l.py` `m/n.py`")
        got = [(p, lab) for p, lab, _, _ in brief_check.labelled_paths(text)]
        self.assertEqual(got, [("a/test_1.py", "NEW"), ("b/test_2.py", "NEW"), ("c/test_3.py", "NEW"),
                               ("d/test_4.py", "NEW"), ("c/test_3.py", ""), ("e/f.py", "existing"),
                               ("g/h.py", "existing"), ("i/j.py", ""), ("k/l.py", ""), ("m/n.py", "")])
        for p, _, start, end in brief_check.labelled_paths(text):
            self.assertTrue(text[start] == "`" and text[end - 1] == "`" and p in text[start:end])

    def test_labelled_paths_does_not_read_NEW_inside_a_longer_word(self):
        self.assertEqual([lab for _, lab, _, _ in brief_check.labelled_paths("RENEW `a/b.py` and `c/d.py` NEWS")], ["", ""])

    def test_labelled_paths_needs_a_space_between_NEW_and_the_token(self):
        # the before rule is \bNEW\s+$: NEW glued to the backtick is no label, with a space it is
        got = [lab for _, lab, _, _ in brief_check.labelled_paths("NEW`scripts/t.py`, see NEW`a/b.py` and NEW `c/d.py`")]
        self.assertEqual(got, ["", "", "NEW"])

    def test_labelled_paths_needs_a_space_between_the_token_and_a_NEW_after_it(self):
        # the after rule is \s+\(?NEW\b: NEW or (NEW) glued to the closing backtick is no label, with a space it is
        got = [lab for _, lab, _, _ in brief_check.labelled_paths(
            "`scripts/a.py`NEW and `scripts/b.py`(NEW) and `scripts/c.py` NEW")]
        self.assertEqual(got, ["", "", "NEW"])

    def test_labelled_paths_reads_the_NEW_before_a_token_case_sensitively(self):
        got = [lab for _, lab, _, _ in brief_check.labelled_paths(
            "new `scripts/a.py` and New `scripts/b.py` and NEW `scripts/c.py`")]
        self.assertEqual(got, ["", "", "NEW"])

    def test_labelled_paths_does_not_read_NEW_ending_an_underscored_name(self):
        # FOO_NEW is one identifier, not the word NEW: the underscore half of the whole word guard (RENEW is the
        # alnum half, test_labelled_paths_does_not_read_NEW_inside_a_longer_word)
        got = [lab for _, lab, _, _ in brief_check.labelled_paths("FOO_NEW `scripts/a.py` and NEW `scripts/b.py`")]
        self.assertEqual(got, ["", "NEW"])

    def test_labelled_paths_reads_the_NEW_after_a_token_case_sensitively(self):
        got = [lab for _, lab, _, _ in brief_check.labelled_paths(
            "`scripts/a.py` new and `scripts/b.py` (New) and `scripts/c.py` NEW")]
        self.assertEqual(got, ["", "", "NEW"])

    def test_labelled_paths_reads_the_real_L0_2_files_block(self):
        block = ("Files:\n- `NEW: scripts/l0_scan.py`\n- `NEW: tests/test_l0_scan.py`\n"
                 "- `NEW: docs/plan/L0-HANDOVER-QUARANTINE.md`\n- `existing: scripts/release_invariant.py`\n")
        self.assertEqual([(p, lab) for p, lab, _, _ in brief_check.labelled_paths(block)],
                         [("scripts/l0_scan.py", "NEW"), ("tests/test_l0_scan.py", "NEW"),
                          ("docs/plan/L0-HANDOVER-QUARANTINE.md", "NEW"), ("scripts/release_invariant.py", "existing")])

    def test_labelled_paths_empty_text_is_an_empty_list(self):
        self.assertEqual(brief_check.labelled_paths(""), [])
        self.assertEqual(brief_check.labelled_paths("no paths here, only NEW words"), [])

    def test_labelled_paths_refuses_non_text(self):
        for bad in (None, 3, b"`a.py`", ["`a.py`"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                brief_check.labelled_paths(bad)

    def brief_with(self, spec, test_path, test_body):
        """A brief whose FILE blocks carry the spec's named test, so one fixture isolates one condition."""
        self.write("spec.md", spec)
        brief = "House laws.\n\n" + WBS + "\nTHE WHOLE UNIT SPECIFICATION:\n" + spec + "\n\nTHE REAL FILES:\n"
        brief += "===== FILE: " + test_path + " =====\n" + test_body
        brief += "===== FILE: scripts/helper.py =====\nx = 1\n"
        return self.write("brief.md", brief)


class LabelledPathsContract(unittest.TestCase):
    """FX-09 round 5: labelled_paths as one table, a row per documented class of its docstring. Rounds 1 to 4 pinned
    survivors one at a time and each verify found new ones; this enumerates the contract instead: counts 0, 1 and
    many (in order, duplicates kept); the four inside spellings and their case; every whitespace \\s+ promises (one
    space, several, tab, newline, form feed, a mix) on BOTH sides of NEW; the word boundary after NEW; case (NEW
    against new and New); and what may precede a NEW read backwards (start of text, whitespace or punctuation read,
    a letter, a digit or an underscore never)."""

    def test_the_label_contract(self):
        t, a, b = "`scripts/a.py`", "scripts/a.py", "scripts/b.py"
        rows = [
            ("0 tokens: empty text", "", []),
            ("0 tokens: words only", "plain words NEW and new", []),
            ("1 token, no label", "see " + t + " here", [(a, "")]),
            ("many tokens in order, duplicates kept", t + " and `b.sh` and " + t, [(a, ""), ("b.sh", ""), (a, "")]),
            # inside the backticks: wins over anything outside, case sensitive
            ("inside NEW: with a space", "`NEW: scripts/a.py`", [(a, "NEW")]),
            ("inside NEW: without a space", "`NEW:scripts/a.py`", [(a, "NEW")]),
            ("inside existing:", "`existing: scripts/a.py`", [(a, "existing")]),
            ("inside (NEW)", "`scripts/a.py (NEW)`", [(a, "NEW")]),
            ("inside (NEW) after two spaces", "`scripts/a.py  (NEW)`", [(a, "NEW")]),
            ("inside (existing)", "`scripts/a.py (existing)`", [(a, "existing")]),
            ("inside existing beats a NEW after", "`existing: scripts/a.py` NEW", [(a, "existing")]),
            ("inside existing beats a NEW before", "NEW `existing: scripts/a.py`", [(a, "existing")]),
            ("inside new: lower case is no label", "`new: scripts/a.py`", [(a, "")]),
            ("inside New: is no label", "`New: scripts/a.py`", [(a, "")]),
            ("inside another word is no label", "`Reads: scripts/a.py`", [(a, "")]),
            ("inside (new) lower case is no token at all", "`scripts/a.py (new)`", []),
            # after the token: \s+\(?NEW\b, matched right at the token's end
            ("after: one space", t + " NEW", [(a, "NEW")]),
            ("after: several spaces", t + "   NEW", [(a, "NEW")]),
            ("after: tab", t + "\tNEW", [(a, "NEW")]),
            ("after: newline", t + "\nNEW", [(a, "NEW")]),
            ("after: form feed", t + "\x0cNEW", [(a, "NEW")]),
            ("after: mixed whitespace", t + " \t\n NEW", [(a, "NEW")]),
            ("after: one space, (NEW)", t + " (NEW)", [(a, "NEW")]),
            ("after: tab, (NEW)", t + "\t(NEW)", [(a, "NEW")]),
            ("after: newline, (NEW)", t + "\n(NEW)", [(a, "NEW")]),
            ("after: two spaces, (NEW)", t + "  (NEW)", [(a, "NEW")]),
            ("after: NEW then punctuation", t + " NEW, then", [(a, "NEW")]),
            ("after: no whitespace", t + "NEW", [(a, "")]),
            ("after: no whitespace, (NEW)", t + "(NEW)", [(a, "")]),
            ("after: NEWS is not the word NEW", t + " NEWS", [(a, "")]),
            ("after: NEW_x is not the word NEW", t + " NEW_x", [(a, "")]),
            ("after: NEW2 is not the word NEW", t + " NEW2", [(a, "")]),
            ("after: a word between is no label", t + " is NEW", [(a, "")]),
            ("after: new lower case", t + " new", [(a, "")]),
            ("after: New", t + " New", [(a, "")]),
            ("after: (new) lower case", t + " (new)", [(a, "")]),
            # before the token: \bNEW\s+$, read backwards from the token
            ("before: start of text, one space", "NEW " + t, [(a, "NEW")]),
            ("before: several spaces", "NEW   " + t, [(a, "NEW")]),
            ("before: tab", "NEW\t" + t, [(a, "NEW")]),
            ("before: newline", "NEW\n" + t, [(a, "NEW")]),
            ("before: two newlines", "NEW\n\n" + t, [(a, "NEW")]),
            ("before: form feed", "NEW\x0c" + t, [(a, "NEW")]),
            ("before: mixed whitespace", "NEW \t\n " + t, [(a, "NEW")]),
            ("before: no whitespace", "NEW" + t, [(a, "")]),
            ("before: punctuation between NEW and the space", "NEW: " + t, [(a, "")]),
            ("before: (NEW) then a space", "(NEW) " + t, [(a, "")]),
            ("before: preceded by a space", "a NEW " + t, [(a, "NEW")]),
            ("before: preceded by a newline", "x\nNEW " + t, [(a, "NEW")]),
            ("before: preceded by (", "(NEW " + t, [(a, "NEW")]),
            ("before: preceded by -", "-NEW " + t, [(a, "NEW")]),
            ("before: preceded by .", "x.NEW " + t, [(a, "NEW")]),
            ("before: preceded by :", "x:NEW " + t, [(a, "NEW")]),
            ("before: a letter at the start of text", "XNEW " + t, [(a, "")]),
            ("before: a letter", "a XNEW " + t, [(a, "")]),
            ("before: RENEW", "RENEW " + t, [(a, "")]),
            ("before: a digit at the start of text", "2NEW " + t, [(a, "")]),
            ("before: a digit", "a 2NEW " + t, [(a, "")]),
            ("before: an underscore at the start of text", "_NEW " + t, [(a, "")]),
            ("before: an underscore", "FOO_NEW " + t, [(a, "")]),
            ("before: new lower case", "new " + t, [(a, "")]),
            ("before: New", "New " + t, [(a, "")]),
            ("before: a leading space only", " " + t, [(a, "")]),
            ("before: whitespace only", " \n " + t, [(a, "")]),
            ("before: EW, too short for NEW", "EW " + t, [(a, "")]),
            # each token is read on its own
            ("one NEW between two tokens labels both", t + " NEW `scripts/b.py`", [(a, "NEW"), (b, "NEW")]),
            ("a list after one NEW labels only the first", "NEW " + t + ", `scripts/b.py`", [(a, "NEW"), (b, "")]),
            # verify-7 rows (2026-09-29): the table left each class to one representative
            ("after: two parentheses is no label", t + " ((NEW", [(a, "")]),
            ("before: a non ASCII letter (e acute) is a word character, no label", "\u00e9NEW " + t, [(a, "")]),
            ("before: a kana is a word character, no label", "\u306fNEW " + t, [(a, "")]),
            ("after: a non ASCII word character right after NEW ends no word", t + " NEW\u00e9", [(a, "")]),
            ("before: NEW starting the text still reads, the text end is never read", "NEW " + t + " x", [(a, "NEW")]),
        ]
        for name, text, expect in rows:
            with self.subTest(name, text=text):
                got = brief_check.labelled_paths(text)
                self.assertEqual([(p, lab) for p, lab, _, _ in got], expect)
                for p, _, start, end in got:                  # start/end is the backticked token's own span
                    m = brief_check.SPEC_PATH.fullmatch(text[start:end])
                    self.assertIsNotNone(m, (text, start, end))
                    self.assertEqual(m.group(1), p)
        spans = [(s, e) for _, _, s, e in brief_check.labelled_paths("x " + t + " y " + t)]
        self.assertEqual(spans, [(2, 2 + len(t)), (5 + len(t), 5 + 2 * len(t))])
        for bad in (None, 3, b"`a.py`", ["`a.py`"]):
            with self.subTest("non text", bad=bad), self.assertRaises(ValueError):
                brief_check.labelled_paths(bad)


if __name__ == "__main__":
    unittest.main()
