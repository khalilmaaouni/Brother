#!/usr/bin/env python3
"""Tests for scripts/cursor_parity.py.

Run: python3 scripts/test_cursor_parity.py -v

Mirrors scripts/test_codex_parity.py (DOM-50.05's test): same shape, same
template, "codex" swapped for "cursor" throughout.

The twelve step names and the three verdict strings are pinned BY HAND
below (EXPECTED_STEP_ORDER, EXPECTED_PASS/FAIL/NODATA), not read back from
adapter_conformance.py, so a change that quietly drops, reorders or renames
one of them fails this suite instead of sailing through with it. Every
cursor_parity.run() and main() case here mocks only
adapter_conformance.run_conformance (the one step that actually shells out
and touches a host); adapter_conformance._verdict_for runs for real, so
these tests prove cursor_parity's own wiring end to end rather than trusting
a second mock of the verdict rule to agree with the real one. No real
subprocess, network or cursor-agent binary is touched anywhere in this file.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import adapter_conformance as AC  # noqa: E402
import cursor_parity as CP  # noqa: E402

# Pinned independently of AC.STEP_ORDER: the twelve step names the shared
# conformance suite runs, in order, at the time this test was written.
EXPECTED_STEP_ORDER = ("install", "same-task", "baseline-red",
                       "changed-files", "receipt-evidence", "exit-semantics",
                       "resume", "install-again", "upgrade", "rollback",
                       "uninstall", "uninstall-again")

EXPECTED_PASS, EXPECTED_FAIL, EXPECTED_NODATA = "PASS", "FAIL", "NO-DATA"


def _step(name, verdict, reason):
    return AC.Step(name, verdict, reason)


class TheSharedSuiteVocabularyIsUnchanged(unittest.TestCase):
    """Pins the vocabulary cursor_parity.py inherits from
    adapter_conformance.py, so a drift in either is caught here rather than
    silently changing this module's own behaviour."""

    def test_step_order_matches_the_pinned_set(self):
        self.assertEqual(AC.STEP_ORDER, EXPECTED_STEP_ORDER)

    def test_verdict_constants_match_the_pinned_strings(self):
        self.assertEqual(CP.PASS, EXPECTED_PASS)
        self.assertEqual(CP.FAIL, EXPECTED_FAIL)
        self.assertEqual(CP.NODATA, EXPECTED_NODATA)

    def test_cursor_parity_reuses_adapter_conformances_own_constants(self):
        # Never a second copy: cursor_parity.PASS/FAIL/NODATA must be the
        # exact same objects adapter_conformance.py defines, not equal
        # strings typed twice in two files.
        self.assertIs(CP.PASS, AC.PASS)
        self.assertIs(CP.FAIL, AC.FAIL)
        self.assertIs(CP.NODATA, AC.NODATA)


class GapListTests(unittest.TestCase):
    def test_all_pass_returns_empty(self):
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("resume", EXPECTED_PASS, "ok")]
        self.assertEqual(CP.gap_list(results), [])

    def test_pass_fail_nodata_pinned_order(self):
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("upgrade", EXPECTED_FAIL, "upgrade broke"),
                  _step("rollback", EXPECTED_NODATA,
                       "offline: rollback not attempted")]
        self.assertEqual(CP.gap_list(results), [
            {"step": "upgrade", "verdict": EXPECTED_FAIL,
             "reason": "upgrade broke"},
            {"step": "rollback", "verdict": EXPECTED_NODATA,
             "reason": "offline: rollback not attempted"},
        ])

    def test_a_different_input_order_is_preserved_not_resorted(self):
        # The reverse of STEP_ORDER: gap_list must not quietly re-sort into
        # STEP_ORDER, it must report exactly the order it was handed,
        # because adapter_conformance.run_conformance already fixed that
        # order and a second opinion about it does not belong here.
        results = [_step("uninstall-again", EXPECTED_NODATA, "z"),
                  _step("install", EXPECTED_FAIL, "a")]
        self.assertEqual(CP.gap_list(results), [
            {"step": "uninstall-again", "verdict": EXPECTED_NODATA,
             "reason": "z"},
            {"step": "install", "verdict": EXPECTED_FAIL, "reason": "a"},
        ])

    def test_unrecognised_verdict_is_included_not_dropped(self):
        # The exact bad state the deciding property forbids: a gap_list
        # that only checks "verdict == FAIL" (or any other narrower test
        # than "verdict != PASS") would let this case vanish silently.
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("resume", "WEIRD", "unrecognised verdict")]
        self.assertEqual(CP.gap_list(results), [
            {"step": "resume", "verdict": "WEIRD",
             "reason": "unrecognised verdict"},
        ])


class RunTests(unittest.TestCase):
    """CP.run() with only adapter_conformance.run_conformance mocked; its
    verdict still comes from the real AC._verdict_for."""

    def _run_with(self, results, **kwargs):
        with mock.patch.object(AC, "run_conformance",
                              return_value=results) as rc:
            verdict, got_results, gaps = CP.run(**kwargs)
        return verdict, got_results, gaps, rc

    def test_all_pass(self):
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("resume", EXPECTED_PASS, "ok")]
        verdict, got_results, gaps, _ = self._run_with(results)
        self.assertEqual(verdict, EXPECTED_PASS)
        self.assertIs(got_results, results)
        self.assertEqual(gaps, [])

    def test_one_nodata_reads_nodata_never_pass(self):
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("rollback", EXPECTED_NODATA, "offline")]
        verdict, _, gaps, _ = self._run_with(results)
        self.assertEqual(verdict, EXPECTED_NODATA)
        self.assertEqual(gaps, [{"step": "rollback",
                                "verdict": EXPECTED_NODATA,
                                "reason": "offline"}])

    def test_fail_beats_nodata(self):
        results = [_step("install", EXPECTED_FAIL, "install broke"),
                  _step("rollback", EXPECTED_NODATA, "offline")]
        verdict, _, gaps, _ = self._run_with(results)
        self.assertEqual(verdict, EXPECTED_FAIL)
        self.assertEqual(len(gaps), 2)

    def test_forwards_every_argument_to_run_conformance_unchanged(self):
        results = [_step("install", EXPECTED_PASS, "ok")]
        self._run_with(results, evidence_dir="/tmp/ev-DOM-50.06",
                       offline=True, marketplace="mkt", ref="v1.2.3",
                       from_ref="v1.2.2")
        _, _, _, rc = self._run_with(
            results, evidence_dir="/tmp/ev-DOM-50.06", offline=True,
            marketplace="mkt", ref="v1.2.3", from_ref="v1.2.2")
        rc.assert_called_with(
            "cursor", "/tmp/ev-DOM-50.06", True,
            marketplace="mkt", ref="v1.2.3", from_ref="v1.2.2")

    def test_no_evidence_dir_falls_back_to_the_default(self):
        results = [_step("install", EXPECTED_PASS, "ok")]
        _, _, _, rc = self._run_with(results)
        rc.assert_called_once_with(
            "cursor", CP.DEFAULT_EVIDENCE_DIR, False,
            marketplace=None, ref=None, from_ref=None)

    def test_an_exception_from_run_conformance_is_never_swallowed(self):
        # cursor_parity.run() adds no try/except of its own: a real failure
        # in the shared suite runner must reach the caller, never be
        # disguised as a clean verdict.
        with mock.patch.object(AC, "run_conformance",
                              side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                CP.run()


class MainTests(unittest.TestCase):
    def _run_main(self, results, argv=None):
        with mock.patch.object(AC, "run_conformance",
                              return_value=results) as rc:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = CP.main(argv or [])
        return code, buf.getvalue(), rc

    def test_all_pass_exits_zero_with_no_gap_lines(self):
        results = [_step("install", EXPECTED_PASS, "ok")]
        code, out, _ = self._run_main(results)
        self.assertEqual(code, 0)
        self.assertNotIn("GAP:", out)
        self.assertIn("cursor parity verdict=PASS gaps=0", out)
        self.assertIn("install", out)

    def test_one_nodata_exits_two_and_names_the_gap(self):
        results = [_step("install", EXPECTED_PASS, "ok"),
                  _step("resume", EXPECTED_NODATA, "no run directory")]
        code, out, _ = self._run_main(results)
        self.assertEqual(code, 2)
        self.assertIn("GAP: resume", out)
        self.assertIn("NO-DATA", out)
        self.assertIn("no run directory", out)
        self.assertIn("cursor parity verdict=NO-DATA gaps=1", out)

    def test_fail_exits_one_and_names_every_gap(self):
        results = [_step("install", EXPECTED_FAIL, "install broke"),
                  _step("resume", EXPECTED_NODATA, "no run directory")]
        code, out, _ = self._run_main(results)
        self.assertEqual(code, 1)
        self.assertIn("GAP: install", out)
        self.assertIn("GAP: resume", out)
        self.assertIn("cursor parity verdict=FAIL gaps=2", out)

    def test_cli_flags_reach_run_conformance_unchanged(self):
        results = [_step("install", EXPECTED_PASS, "ok")]
        _, _, rc = self._run_main(
            results, argv=["--evidence-dir", "/tmp/ev-cli",
                          "--offline", "--marketplace", "mkt2",
                          "--ref", "v2", "--from-ref", "v1"])
        rc.assert_called_once_with(
            "cursor", "/tmp/ev-cli", True,
            marketplace="mkt2", ref="v2", from_ref="v1")


class RealAdapterIsRegistered(unittest.TestCase):
    """Unlike the mocked tests above, this proves scripts/provider_adapter.py
    actually carries a "cursor" entry adapter_conformance.run_conformance
    can look up: DOM-50.06's whole point, that the suite has a cursor
    provider at all."""

    def test_cursor_is_a_real_provider(self):
        import provider_adapter as PA  # noqa: E402
        self.assertIn("cursor", PA.ADAPTERS)


class AnOfflineRunNamesExactlySixLifecycleGaps(unittest.TestCase):
    """Real, non-mocked regression test: calls cursor_parity.run(offline=True)
    against the real adapter_conformance.run_conformance, the way
    `python3 scripts/cursor_parity.py --offline` is actually invoked, using
    the module's own default evidence directory (an empty or overridden
    evidence_dir changes what steps 2-7 can observe and is not what a real
    invocation does, so writing into the shared
    ~/.claude/evidence/adapter-conformance/cursor directory instead of an
    isolated temp dir is deliberate here, not an oversight: isolating it
    would change the very thing this regression test exists to pin).
    Offline mode forces the four lifecycle verbs (install/upgrade/
    rollback/uninstall and their -again siblings) to NO-DATA by
    construction (adapter_conformance.py's own ctx["offline"] check), but
    ONLY once CursorAdapter.invocation() itself has succeeded: when no
    cursor-agent binary is found on PATH or ~/.local/bin/cursor-agent,
    invocation() returns a Refusal and adapter_conformance.run_conformance's
    own Refusal branch marks ALL TWELVE steps NO-DATA instead of the six
    lifecycle-only steps pinned below (a real environment fact, not a
    defect in either module). This test checks that fact directly, via
    CursorAdapter().invocation() itself, before asserting anything: never a
    bare except swallowing an actual invocation() regression, which would
    contradict its sibling test_an_exception_from_run_conformance_is_never_
    swallowed. This is the test the review that approved DOM-50.06 asked
    for: it would go red the moment a hook-removal or an always-Refusal
    invocation() regression silently changed which steps are gaps, which
    every prior test in this file mocked away."""

    EXPECTED_GAP_STEPS = frozenset([
        "install", "install-again", "upgrade", "rollback",
        "uninstall", "uninstall-again",
    ])

    def test_offline_run_is_no_data_with_the_six_named_lifecycle_gaps(self):
        import provider_adapter as PA  # noqa: E402
        # A real, direct environment check, not a caught exception: only
        # "cursor-agent is genuinely absent" (invocation() itself refuses)
        # is grounds to skip. Anything else, including an exception raised
        # by CP.run() below, is a regression and must fail red.
        invocation = PA.CursorAdapter(env=os.environ).invocation()
        if isinstance(invocation, PA.Refusal):
            self.skipTest(
                "NO-DATA: no cursor-agent binary found on PATH or "
                "~/.local/bin/cursor-agent on this machine (%s); when "
                "invocation() refuses, adapter_conformance marks all "
                "twelve steps NO-DATA, not the six lifecycle-only gaps "
                "this test pins, so there is nothing to assert here until "
                "cursor-agent is installed" % (invocation.reason,))
        verdict, results, gaps = CP.run(offline=True)
        self.assertEqual(verdict, EXPECTED_NODATA,
                         "offline run must never read as PASS or FAIL; "
                         "got %r with gaps %r" % (verdict, gaps))
        gap_steps = frozenset(g["step"] for g in gaps)
        self.assertEqual(gap_steps, self.EXPECTED_GAP_STEPS)
        fail_gaps = [g for g in gaps if g["verdict"] == EXPECTED_FAIL]
        self.assertEqual(fail_gaps, [],
                         "offline mode must never produce a real FAIL: %r"
                         % (fail_gaps,))


if __name__ == "__main__":
    unittest.main()
