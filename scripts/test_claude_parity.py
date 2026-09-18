#!/usr/bin/env python3
"""Tests for scripts/claude_parity.py (DOM-50.04).

Runnable directly: python3 scripts/test_claude_parity.py -v

No network and no real claude CLI call: every test injects a stub for
scripts/adapter_conformance.run_conformance (the one function that would
otherwise shell out to brother_run.py, the claude CLI or brother_install.py)
and patches scripts/host_capability.CAPABILITY_TABLE in place, rather than
letting claude_parity.claude_parity() run the real twelve-step suite.

The nine categories are pinned here as a literal tuple, per the worker
contract's TEST RULE, and never read back from claude_parity.CATEGORIES:
a rename or a dropped category inside the module must fail this test, not
silently redefine what "every behaviour" means.
"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import claude_parity  # noqa: E402
import adapter_conformance as AC  # noqa: E402
import host_capability  # noqa: E402

PINNED_CATEGORIES = ("install", "upgrade", "uninstall", "hooks",
                     "enforcement", "resume", "subagents", "evidence",
                     "receipts")


def _steps(overrides=None):
    """One adapter_conformance.Step per STEP_ORDER name, PASS by default,
    with `overrides` ({name: (verdict, reason)}) applied on top. Mirrors
    a real run_conformance() return value without calling it."""
    overrides = overrides or {}
    out = []
    for name in AC.STEP_ORDER:
        verdict, reason = overrides.get(name, (AC.PASS, "%s ok" % name))
        out.append(AC.Step(name, verdict, reason))
    return out


class CategoriesPinned(unittest.TestCase):
    def test_categories_match_pinned_set(self):
        self.assertEqual(claude_parity.CATEGORIES, PINNED_CATEGORIES)

    def test_every_category_has_exactly_one_source(self):
        sources = (set(claude_parity._STEP_CATEGORIES)
                  | set(claude_parity._TABLE_CATEGORIES)
                  | set(claude_parity._UNMEASURED))
        self.assertEqual(sources, set(PINNED_CATEGORIES))
        # No category is double-sourced.
        total = (len(claude_parity._STEP_CATEGORIES)
                + len(claude_parity._TABLE_CATEGORIES)
                + len(claude_parity._UNMEASURED))
        self.assertEqual(total, len(PINNED_CATEGORIES))


class StepBackedCategories(unittest.TestCase):
    """install, upgrade, uninstall, resume, receipts, evidence."""

    def _run(self, overrides=None):
        with mock.patch.object(AC, "run_conformance",
                               return_value=_steps(overrides)):
            return claude_parity.claude_parity(evidence_dir="/unused",
                                               offline=True)

    def test_all_steps_pass_gives_pass(self):
        results = self._run()
        for cat in ("install", "upgrade", "uninstall", "resume",
                   "receipts", "evidence"):
            verdict, _ = results[cat]
            self.assertEqual(verdict, claude_parity.PASS, cat)

    def test_one_failed_step_fails_its_category_only(self):
        # THE BAD STATE A LOOSER CHECK WOULD ALSO PASS: a checker that
        # only looked at whether ANY step passed, rather than requiring
        # the named step for THIS category to pass, would read "install"
        # as fine here even though its own step failed. This test would
        # not catch that looser checker; the assertion on "upgrade"
        # staying PASS is what proves categories are not smeared together.
        results = self._run({"install": (AC.FAIL, "brother_install.py "
                                        "install exited 3")})
        self.assertEqual(results["install"][0], claude_parity.FAIL)
        self.assertIn("exited 3", results["install"][1])
        self.assertEqual(results["upgrade"][0], claude_parity.PASS)

    def test_nodata_step_is_nodata_not_pass(self):
        results = self._run({"resume": (AC.NODATA, "no run directory")})
        self.assertEqual(results["resume"][0], claude_parity.NODATA)

    def test_missing_step_reads_nodata_never_pass(self):
        # A step name adapter_conformance.STEP_ORDER dropped (a suite
        # rewrite this module was not updated for) must never silently
        # read as PASS.
        steps = [s for s in _steps() if s.name != "uninstall"]
        with mock.patch.object(AC, "run_conformance", return_value=steps):
            results = claude_parity.claude_parity(evidence_dir="/unused",
                                                  offline=True)
        verdict, reason = results["uninstall"]
        self.assertEqual(verdict, claude_parity.NODATA)
        self.assertIn("uninstall", reason)

    def test_evidence_fails_if_either_half_fails(self):
        results = self._run({"changed-files": (AC.FAIL, "byte identical")})
        self.assertEqual(results["evidence"][0], claude_parity.FAIL)

    def test_evidence_nodata_if_either_half_nodata_and_none_failed(self):
        results = self._run({"baseline-red": (AC.NODATA, "no run dir")})
        self.assertEqual(results["evidence"][0], claude_parity.NODATA)

    def test_evidence_requires_both_halves_pass(self):
        results = self._run({
            "baseline-red": (AC.PASS, "ok"),
            "changed-files": (AC.PASS, "ok"),
        })
        self.assertEqual(results["evidence"][0], claude_parity.PASS)

    def test_evidence_both_fail_is_fail(self):
        results = self._run({
            "baseline-red": (AC.FAIL, "no run dir"),
            "changed-files": (AC.FAIL, "byte identical"),
        })
        self.assertEqual(results["evidence"][0], claude_parity.FAIL)

    def test_evidence_both_nodata_is_nodata(self):
        results = self._run({
            "baseline-red": (AC.NODATA, "no run dir"),
            "changed-files": (AC.NODATA, "no toy"),
        })
        self.assertEqual(results["evidence"][0], claude_parity.NODATA)

    def test_unrecognised_step_verdict_raises(self):
        # Muse adversarial review of the DeepSeek draft, findings 2/3/12,
        # confirmed against this integrated code: a step verdict outside
        # PASS/FAIL/NO-DATA must never fall through to a silent PASS.
        bad_steps = _steps({"install": ("MAYBE", "a typo in a future step")})
        with mock.patch.object(AC, "run_conformance", return_value=bad_steps):
            with self.assertRaises(ValueError):
                claude_parity.claude_parity(evidence_dir="/unused",
                                           offline=True)


class TableBackedCategories(unittest.TestCase):
    """hooks, enforcement, read from host_capability.CAPABILITY_TABLE."""

    def _run_with_table(self, claude_row):
        with mock.patch.object(AC, "run_conformance", return_value=_steps()), \
             mock.patch.dict(host_capability.CAPABILITY_TABLE,
                             {"claude": claude_row}, clear=False):
            return claude_parity.claude_parity(evidence_dir="/unused",
                                               offline=True)

    def test_yes_text_is_pass(self):
        results = self._run_with_table({
            "pre_tool_hook": "yes: PreToolUse, measured",
            "enforceable_deny": "yes, measured: bm_fence_hook.py",
        })
        self.assertEqual(results["hooks"][0], claude_parity.PASS)
        self.assertEqual(results["enforcement"][0], claude_parity.PASS)

    def test_nodata_marker_text_is_nodata(self):
        results = self._run_with_table({
            "pre_tool_hook": host_capability.NODATA + ": not measured",
            "enforceable_deny": "yes: measured",
        })
        self.assertEqual(results["hooks"][0], claude_parity.NODATA)

    def test_neither_yes_nor_nodata_is_fail(self):
        # THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a reducer that
        # defaulted anything unrecognised to PASS (rather than FAIL) would
        # read a malformed table entry as evidence of a working hook. This
        # is exactly the permissive-default the estate's rules forbid.
        results = self._run_with_table({
            "pre_tool_hook": "maybe, unclear",
            "enforceable_deny": "yes: measured",
        })
        self.assertEqual(results["hooks"][0], claude_parity.FAIL)

    def test_missing_field_is_nodata(self):
        results = self._run_with_table({"enforceable_deny": "yes: measured"})
        self.assertEqual(results["hooks"][0], claude_parity.NODATA)

    def test_leading_whitespace_yes_is_still_pass(self):
        # Muse finding 4, confirmed against this integrated code: a
        # stripped comparison must not turn a real "yes" into a FAIL.
        results = self._run_with_table({
            "pre_tool_hook": "  yes: PreToolUse, measured",
            "enforceable_deny": "yes: measured",
        })
        self.assertEqual(results["hooks"][0], claude_parity.PASS)

    def test_yes_prefixed_word_is_not_a_pass(self):
        # Muse finding 1, confirmed against this integrated code: only the
        # whole word "yes" counts, so "yesterday" must read FAIL, not PASS.
        results = self._run_with_table({
            "pre_tool_hook": "yesterday this was measured",
            "enforceable_deny": "yes: measured",
        })
        self.assertEqual(results["hooks"][0], claude_parity.FAIL)

    def test_missing_host_row_is_nodata_for_both(self):
        with mock.patch.object(AC, "run_conformance", return_value=_steps()), \
             mock.patch.dict(host_capability.CAPABILITY_TABLE, {}, clear=True):
            results = claude_parity.claude_parity(evidence_dir="/unused",
                                                  offline=True)
        self.assertEqual(results["hooks"][0], claude_parity.NODATA)
        self.assertEqual(results["enforcement"][0], claude_parity.NODATA)


class SubagentsUnmeasured(unittest.TestCase):
    def test_subagents_is_always_nodata(self):
        with mock.patch.object(AC, "run_conformance", return_value=_steps()):
            results = claude_parity.claude_parity(evidence_dir="/unused",
                                                  offline=True)
        verdict, reason = results["subagents"]
        self.assertEqual(verdict, claude_parity.NODATA)
        self.assertTrue(reason)

    def test_subagents_stays_nodata_even_if_every_step_passes(self):
        # THE BAD STATE A GREEN CHECK WOULD ALSO PASS: a reducer that
        # inferred "subagents" from a generally-passing conformance run
        # would fabricate a pass for a behaviour nothing actually measured.
        steps = _steps({name: (AC.PASS, "ok") for name in AC.STEP_ORDER})
        with mock.patch.object(AC, "run_conformance", return_value=steps):
            results = claude_parity.claude_parity(evidence_dir="/unused",
                                                  offline=True)
        self.assertEqual(results["subagents"][0], claude_parity.NODATA)


class OverallVerdict(unittest.TestCase):
    def test_fail_beats_nodata_beats_pass(self):
        all_pass = {c: (claude_parity.PASS, "") for c in PINNED_CATEGORIES}
        self.assertEqual(claude_parity.overall_verdict(all_pass),
                         claude_parity.PASS)

        with_nodata = dict(all_pass)
        with_nodata["subagents"] = (claude_parity.NODATA, "")
        self.assertEqual(claude_parity.overall_verdict(with_nodata),
                         claude_parity.NODATA)

        with_fail = dict(with_nodata)
        with_fail["install"] = (claude_parity.FAIL, "")
        self.assertEqual(claude_parity.overall_verdict(with_fail),
                         claude_parity.FAIL)

    def test_empty_results_raises_not_silent_pass(self):
        # Muse finding 2, confirmed against this integrated code: an
        # empty or incomplete report must not read as a clean PASS.
        with self.assertRaises(ValueError):
            claude_parity.overall_verdict({})

    def test_unknown_verdict_string_raises_not_silent_pass(self):
        # Muse finding 3, confirmed against this integrated code.
        bad = {c: (claude_parity.PASS, "") for c in PINNED_CATEGORIES}
        bad["hooks"] = ("MAYBE", "not a real verdict")
        with self.assertRaises(ValueError):
            claude_parity.overall_verdict(bad)


class MainExitCodes(unittest.TestCase):
    def _run_main(self, results):
        import io
        import contextlib
        buf = io.StringIO()
        with mock.patch.object(claude_parity, "claude_parity",
                               return_value=results), \
             contextlib.redirect_stdout(buf):
            code = claude_parity.main([])
        return code, buf.getvalue()

    def test_all_pass_exits_zero(self):
        results = {c: (claude_parity.PASS, "ok") for c in PINNED_CATEGORIES}
        code, out = self._run_main(results)
        self.assertEqual(code, 0)
        self.assertIn("verdict=PASS", out)
        # One line per category, per the deciding property: every one of
        # the nine named behaviours is visible in the printed report.
        for category in PINNED_CATEGORIES:
            self.assertIn(category, out)

    def test_any_fail_exits_one(self):
        results = {c: (claude_parity.PASS, "ok") for c in PINNED_CATEGORIES}
        results["hooks"] = (claude_parity.FAIL, "broken")
        code, out = self._run_main(results)
        self.assertEqual(code, 1)
        self.assertIn("verdict=FAIL", out)

    def test_nodata_without_fail_exits_two(self):
        results = {c: (claude_parity.PASS, "ok") for c in PINNED_CATEGORIES}
        results["subagents"] = (claude_parity.NODATA, "unmeasured")
        code, out = self._run_main(results)
        self.assertEqual(code, 2)
        self.assertIn("verdict=NO-DATA", out)


if __name__ == "__main__":
    unittest.main()
