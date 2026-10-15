#!/usr/bin/env python3
"""LL1.c: the pool refuses a sub unit whose section carries a PROTECTED-TOUCH or OWNS finding, in every lint mode.

The refusal lives in ONE place (runner_pool.lint_refusal, called from admissible), so the pass and both other
callers (diag_apply.py, salvage.py) inherit it with no edit. BROTHER_RUNFLOW_LINT keeps its meaning for every other
lint rule; this one refuses whatever it says.

Run: python3 -B scripts/loop/test_runner_pool_lint_refusal.py
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import runner_pool as RP  # noqa: E402
import plan_lint  # noqa: E402

MODES = (None, "report", "block", "nonsense-not-a-mode")


def _spec_files(sub, files, built_by="Brother.Loop"):
    """One spec section for `sub`, on disk text: loop built by default, one Files line naming `files`."""
    return ("# fixture unit\n\n## %s\nBuilt by: %s\nFiles: %s\n"
            "Done check: `python3 -B -m unittest tests.test_x`\n") % (sub, built_by, files)


def _write(text):
    """A temporary directory holding one spec file, named by its absolute path (the guard suite's own shape)."""
    d = tempfile.mkdtemp(prefix="ll1c-")
    path = os.path.join(d, "SPEC.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _plan(spec_path, sub, owns):
    """A hand built plan with one open unit A whose one open sub unit is `sub` and whose spec is an absolute path."""
    return {"units": [{
        "id": "A", "state": "OPEN", "spec": spec_path, "evidence": "",
        "owns": list(owns),
        "sub_units": [{"id": sub, "state": "OPEN"}],
    }]}


class TestLL1C(unittest.TestCase):

    def _admissible_in_modes(self, plan, sub):
        """admissible once per lint mode, with the landing gate's own done check pinned so the case under test is the
        lint refusal alone; the spec file on disk is still read, so hard_findings sees exactly what the section says."""
        out = []
        with mock.patch.object(RP.spec_check, "gate_refusal", return_value=""):
            for mode in MODES:
                if mode is None:
                    with mock.patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("BROTHER_RUNFLOW_LINT", None)
                        out.append(RP.admissible(plan, sub))
                else:
                    with mock.patch.dict(os.environ, {"BROTHER_RUNFLOW_LINT": mode}, clear=False):
                        out.append(RP.admissible(plan, sub))
        return out

    def test_a_protected_touch_is_refused_in_every_lint_mode(self):
        path = _write(_spec_files("A.a", "`scripts/check_all.sh`"))
        plan = _plan(path, "A.a", ["scripts/check_all.sh"])
        answers = self._admissible_in_modes(plan, "A.a")
        self.assertEqual(len(answers), len(MODES))
        for mode, got in zip(MODES, answers):
            self.assertTrue(got.startswith("LINT PROTECTED-TOUCH"), "mode %r gave %r" % (mode, got))

    def test_an_owns_finding_is_refused_in_every_lint_mode(self):
        path = _write(_spec_files("A.a", "`scripts/zz_fixture.py`"))
        plan = _plan(path, "A.a", ["scripts/other.py"])
        answers = self._admissible_in_modes(plan, "A.a")
        self.assertEqual(len(answers), len(MODES))
        for mode, got in zip(MODES, answers):
            self.assertTrue(got.startswith("LINT OWNS"), "mode %r gave %r" % (mode, got))

    def test_the_refusal_is_the_finding_line_without_start(self):
        finding = {"unit": "A", "sub": "A.a", "rule": "PROTECTED-TOUCH", "severity": "BLOCKING",
                   "text": "START was here", "fix": "change Start now"}
        with mock.patch.object(plan_lint, "hard_findings", return_value=[finding]):
            got = RP.lint_refusal({"id": "A", "owns": ["scripts/x.py"]}, "A.a", "irrelevant")
        self.assertEqual(got, "LINT PROTECTED-TOUCH: Start was here | FIX: change Start now")
        self.assertNotIn("START", got)

    def test_a_clean_section_is_admitted(self):
        path = _write(_spec_files("A.a", "`scripts/zz_fixture.py`"))
        plan = _plan(path, "A.a", ["scripts/zz_fixture.py"])
        with mock.patch.object(RP.spec_check, "gate_refusal", return_value=""):
            self.assertEqual(RP.admissible(plan, "A.a"), "")

    def test_an_advisory_only_finding_is_admitted(self):
        finding = {"unit": "A", "sub": "A.a", "rule": "EV", "severity": "ADVISORY",
                   "text": "cost is high", "fix": "review the cost"}
        with mock.patch.object(plan_lint, "hard_findings", return_value=[finding]):
            got = RP.lint_refusal({"id": "A", "owns": ["scripts/x.py"]}, "A.a", "irrelevant")
        self.assertEqual(got, "")

    def test_a_lint_crash_refuses_with_no_data(self):
        with mock.patch.object(plan_lint, "hard_findings", side_effect=RuntimeError("boom")):
            got = RP.lint_refusal({"id": "A", "owns": ["scripts/x.py"]}, "A.a", "irrelevant")
        self.assertEqual(got, "NO-DATA the lint could not run (RuntimeError)")

    def test_the_pass_and_both_other_callers_route_through_admissible(self):
        with open(os.path.join(HERE, "runner_pool.py"), encoding="utf-8") as fh:
            src = fh.read()
        start = src.find("def admissible(plan, sub):")
        self.assertNotEqual(start, -1, "admissible must stay defined in runner_pool.py")
        nxt = src.find("\ndef ", start + 1)
        body = src[start:nxt if nxt != -1 else len(src)]
        self.assertIn("lint_refusal(unit, sub, spec_text)", body,
                      "admissible itself must call lint_refusal so every caller inherits the refusal")
        for name in ("diag_apply.py", "salvage.py"):
            for parent in (HERE, os.path.dirname(HERE)):
                candidate = os.path.join(parent, name)
                if os.path.isfile(candidate):
                    with open(candidate, encoding="utf-8") as fh:
                        self.assertIn("admissible", fh.read(),
                                      "%s routes through admissible by name" % candidate)

    def test_a_sub_unit_with_no_section_is_not_refused_by_this_rule(self):
        path = _write(_spec_files("B.b", "`scripts/check_all.sh`"))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        unit = {"id": "A", "owns": ["scripts/check_all.sh"], "spec": path}
        self.assertEqual(RP.lint_refusal(unit, "A.a", text), "")

    def test_hostile_input_is_refused_not_crashed(self):
        nasty = (None, 0, True, -1, float("nan"), "", "x", b"x", [], ["x"], {}, {"a": 1}, (), {1, 2}, object())
        for value in nasty:
            self.assertIsInstance(RP.lint_refusal(value, value, value), str)
            self.assertIsInstance(RP.admissible(value, value), str)


if __name__ == "__main__":
    unittest.main()
