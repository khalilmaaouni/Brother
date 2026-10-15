#!/usr/bin/env python3
"""CT1.d: sibling_new names a later sibling's NEW label of a path an earlier sibling already creates.

Run: python3 -B scripts/loop/test_sibling_new.py
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _section_of(spec, sub):
    """The text of the section headed `### <sub> ...`, or None when there is no such section."""
    if not isinstance(spec, str) or not isinstance(sub, str):
        return None
    collecting = False
    out = []
    for line in spec.splitlines(True):
        if line.startswith("### "):
            if collecting:
                break
            head = line[4:].strip()
            parts = head.split()
            name = parts[0] if parts else ""
            if name == sub:
                collecting = True
            continue
        if collecting:
            out.append(line)
    if not collecting:
        return None
    return "".join(out)


def _labelled_paths(text):
    """[(path, label, start, end)] for every `<path>` (NEW) or `<path>` (existing) in text."""
    if not isinstance(text, str):
        return []
    out = []
    i = 0
    while True:
        open_tick = text.find("`", i)
        if open_tick < 0:
            break
        close_tick = text.find("`", open_tick + 1)
        if close_tick < 0:
            break
        path = text[open_tick + 1:close_tick]
        after = text[close_tick + 1:]
        label = None
        for name in ("NEW", "existing"):
            token = "(%s)" % name
            if after.startswith(" " + token):
                label = name
                break
        if label is not None:
            out.append((path, label, open_tick, close_tick + 1))
        i = close_tick + 1
    return out


class _BriefCheckStub(object):
    """The two readers the sibling_new rule imports, stubbed so this suite exercises sibling_new itself.

    The real brief checker beside this file has its own suite (scripts/test_brief_check.py); its exact
    section and label syntax is not this test's subject, only that sibling_new calls it by these names.
    """
    section_of = staticmethod(_section_of)
    labelled_paths = staticmethod(_labelled_paths)


sys.modules["brief_check"] = _BriefCheckStub()

import sibling_new  # noqa: E402


_ROOT = os.path.dirname(os.path.dirname(HERE))
_CT1_PLAN = os.path.join(_ROOT, "docs", "plan", "BROTHER-1.1.4-WBS.json")


class SiblingNewConflictsTests(unittest.TestCase):
    """REQ-CT1-D-01 to REQ-CT1-D-04: the rule itself."""

    def test_a_later_sub_unit_that_labels_a_sibling_file_new_is_named(self):
        spec = ("### A\nFiles: `scripts/x.py` (NEW)\n"
                "### B\nFiles: `scripts/x.py` (NEW), `scripts/y.py` (NEW)\n")
        self.assertEqual(sibling_new.sibling_new_conflicts(spec, ["A", "B"]),
                         [("B", "scripts/x.py", "A")])

    def test_existing_labels_and_a_repeat_inside_one_section_are_clean(self):
        spec_existing = ("### A\nFiles: `scripts/x.py` (existing)\n"
                         "### B\nFiles: `scripts/x.py` (existing)\n")
        self.assertEqual(sibling_new.sibling_new_conflicts(spec_existing, ["A", "B"]), [])
        spec_repeat = "### A\nFiles: `scripts/y.py` (NEW), `scripts/y.py` (NEW)\n"
        self.assertEqual(sibling_new.sibling_new_conflicts(spec_repeat, ["A"]), [])

    def test_a_sub_with_no_section_is_skipped_and_order_is_kept(self):
        spec = ("### A\nFiles: `scripts/x.py` (NEW)\n"
                "### C\nFiles: `scripts/x.py` (NEW)\n")
        self.assertEqual(sibling_new.sibling_new_conflicts(spec, ["B", "A", "C"]),
                         [("C", "scripts/x.py", "A")])
        self.assertEqual(sibling_new.sibling_new_conflicts(spec, ["C", "A"]),
                         [("A", "scripts/x.py", "C")])

    def test_hostile_arguments_are_refused(self):
        values = (None, 0, True, False, -1, float("nan"), b"x", "", "x",
                  [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
        for bad in values:
            with self.assertRaises(ValueError):
                sibling_new.sibling_new_conflicts(bad, bad)
        for bad in (None, 0, True, -1, float("nan"), b"x", "x",
                    {}, {"a": 1}, (), {1, 2}, object()):
            with self.assertRaises(ValueError):
                sibling_new.sibling_new_conflicts("", bad)
        with self.assertRaises(ValueError):
            sibling_new.sibling_new_conflicts("", ["ok", 5])
        with self.assertRaises(ValueError):
            sibling_new.sibling_new_conflicts("", [object()])


class MainTests(unittest.TestCase):
    """REQ-CT1-D-05: exit 1 on a conflict, 0 clean, 2 on an unreadable plan or spec."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sibling-new-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _invoke(self, argv):
        buf = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = sibling_new.main(argv)
        return rc, buf.getvalue()

    def test_main_exits_one_zero_and_two(self):
        spec_conflict = self._write("conflict.md",
            "### A\nFiles: `scripts/x.py` (NEW)\n### B\nFiles: `scripts/x.py` (NEW)\n")
        plan_conflict = self._write("conflict.json", json.dumps({
            "units": [{"id": "CT1", "spec": spec_conflict, "sub_units": ["A", "B"]}]}))
        rc, out = self._invoke([plan_conflict])
        self.assertEqual(rc, 1, out)
        self.assertIn("NEW-SIBLING CT1: B labels scripts/x.py NEW, already created by A", out)

        spec_clean = self._write("clean.md",
            "### A\nFiles: `scripts/x.py` (NEW)\n### B\nFiles: `scripts/y.py` (NEW)\n")
        plan_clean = self._write("clean.json", json.dumps({
            "units": [{"id": "CT1", "spec": spec_clean, "sub_units": ["A", "B"]}]}))
        rc, out = self._invoke([plan_clean])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out, "")

        rc, _ = self._invoke([os.path.join(self.tmp, "absent.json")])
        self.assertEqual(rc, 2)

        plan_bad_spec = self._write("bad-spec.json", json.dumps({
            "units": [{"id": "CT1", "spec": os.path.join(self.tmp, "missing.md"),
                       "sub_units": ["A", "B"]}]}))
        rc, _ = self._invoke([plan_bad_spec])
        self.assertEqual(rc, 2)

        rc, _ = self._invoke([])
        self.assertEqual(rc, 2)
        rc, _ = self._invoke([plan_clean, plan_clean])
        self.assertEqual(rc, 2)

    def test_main_hostile_argv_is_refused_never_a_crash(self):
        saved = sys.argv
        sys.argv = ["sibling_new.py"]
        try:
            rc, _ = self._invoke(None)
            self.assertEqual(rc, 2)
        finally:
            sys.argv = saved
        for bad in (0, True, -1, 1.5, float("nan"), b"x", "x", {}, {"a": 1},
                    (), {1, 2}, object()):
            rc, _ = self._invoke(bad)
            self.assertEqual(rc, 2, repr(bad))
        for bad in (5, None, object(), b"x"):
            rc, _ = self._invoke([bad])
            self.assertEqual(rc, 2, repr(bad))


class TheCt1SpecTests(unittest.TestCase):
    """REQ-CT1-D-06: this unit's own plan is clean under the rule."""

    @unittest.skipUnless(os.path.isfile(_CT1_PLAN), "the CT1 plan is not in this tree")
    def test_the_ct1_spec_is_clean_under_its_own_rule(self):
        saved = os.getcwd()
        os.chdir(_ROOT)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = sibling_new.main([_CT1_PLAN])
        finally:
            os.chdir(saved)
        self.assertEqual(rc, 0, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
