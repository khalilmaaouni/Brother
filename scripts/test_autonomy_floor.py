#!/usr/bin/env python3
"""Tests for DOM-40.07, scripts/autonomy_floor.py. Run directly:
python3 scripts/test_autonomy_floor.py -v

THE PINNED TABLE is written by hand below, not read back from
autonomy_floor.REQUIRED_CAPABILITIES: comparing the module's dict against
this literal is the one check that would catch a silent drift; deriving
the expectation from the module itself would make that check unable to
fail, which the worker contract's rule 5 forbids ("a test that cannot fail
is not evidence"). Every level, and every capability that level requires,
is also driven directly through floor_verdict(), not just asserted present
in the table.
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autonomy_dial
import autonomy_floor
import capability_precheck
import execution_boundary
import host_adapter_spec


# Written by hand against docs/plan/ORCH-1020-WBS.json's deciding property,
# not copied from autonomy_floor.py.
EXPECTED_REQUIRED_CAPABILITIES = {
    "A0": ("filesystem", "network", "credential", "process"),
    "A1": ("credential", "process"),
    "A2": (),
    "A3": (),
}


def _claim(level, evidence=None):
    report = {"claim": level}
    if evidence is not None:
        report["evidence"] = evidence
    return report


ALL_ENFORCED_WITH_EVIDENCE = {
    name: _claim(host_adapter_spec.ENFORCED, "measured")
    for name in execution_boundary.CAPABILITIES
}


class PinnedTableTest(unittest.TestCase):
    def test_table_matches_hand_written_expectation(self):
        self.assertEqual(
            autonomy_floor.REQUIRED_CAPABILITIES,
            EXPECTED_REQUIRED_CAPABILITIES)

    def test_table_covers_exactly_autonomy_dial_order(self):
        # A bad edit could add or drop a level; autonomy_dial.ORDER is the
        # one place levels are enumerated, so the table must match it
        # exactly, not just be a subset or superset.
        self.assertEqual(
            set(autonomy_floor.REQUIRED_CAPABILITIES), set(autonomy_dial.ORDER))

    def test_every_pinned_capability_is_a_real_capability(self):
        for level, caps in EXPECTED_REQUIRED_CAPABILITIES.items():
            for cap in caps:
                self.assertIn(cap, execution_boundary.CAPABILITIES,
                              "level %s names %r" % (level, cap))


class UnknownLevelRefusesTest(unittest.TestCase):
    def test_unrecognised_string_refuses(self):
        verdict, reason = autonomy_floor.floor_verdict("A4", ALL_ENFORCED_WITH_EVIDENCE)
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertIn("unknown autonomy level", reason)

    def test_lowercase_is_not_normalised_and_refuses(self):
        # A permissive implementation might .upper() the input; the
        # deciding property says an unknown level refuses, so this must
        # NOT be silently treated as "a0".
        verdict, _reason = autonomy_floor.floor_verdict("a0", ALL_ENFORCED_WITH_EVIDENCE)
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_none_refuses(self):
        verdict, _reason = autonomy_floor.floor_verdict(None, ALL_ENFORCED_WITH_EVIDENCE)
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_empty_string_refuses(self):
        verdict, _reason = autonomy_floor.floor_verdict("", ALL_ENFORCED_WITH_EVIDENCE)
        self.assertEqual(verdict, autonomy_floor.REFUSE)


class NoFloorLevelsRunUnconditionallyTest(unittest.TestCase):
    """A2 and A3 require nothing: capability_precheck's own precedent for
    a unit requiring nothing is DISPATCH unconditionally, even on a NO-DATA
    report, and this module must carry that through for its two empty
    levels."""

    def test_a2_runs_with_empty_report(self):
        verdict, _reason = autonomy_floor.floor_verdict("A2", {})
        self.assertEqual(verdict, autonomy_floor.RUN)

    def test_a3_runs_with_unreadable_report(self):
        verdict, _reason = autonomy_floor.floor_verdict("A3", "not a mapping")
        self.assertEqual(verdict, autonomy_floor.RUN)

    def test_a2_runs_with_none_report(self):
        verdict, _reason = autonomy_floor.floor_verdict("A2", None)
        self.assertEqual(verdict, autonomy_floor.RUN)


class UnreadableReportRefusesTest(unittest.TestCase):
    def test_none_refuses_for_a0(self):
        verdict, reason = autonomy_floor.floor_verdict("A0", None)
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertIn(host_adapter_spec.NODATA, reason)

    def test_string_refuses_for_a1(self):
        verdict, reason = autonomy_floor.floor_verdict("A1", "nope")
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertIn(host_adapter_spec.NODATA, reason)

    def test_list_refuses_for_a0(self):
        verdict, _reason = autonomy_floor.floor_verdict("A0", ["not", "a", "mapping"])
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_empty_dict_refuses_for_a0_missing_every_capability(self):
        # A valid mapping naming nothing is different from an unreadable
        # report: every required capability reads UNKNOWN, refused.
        verdict, reason = autonomy_floor.floor_verdict("A0", {})
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertNotIn(host_adapter_spec.NODATA, reason)


class A0RequiresAllFourEnforcedTest(unittest.TestCase):
    def test_all_four_enforced_with_evidence_runs(self):
        verdict, reason = autonomy_floor.floor_verdict("A0", ALL_ENFORCED_WITH_EVIDENCE)
        self.assertEqual(verdict, autonomy_floor.RUN, reason)

    def test_missing_one_of_four_refuses(self):
        for missing in execution_boundary.CAPABILITIES:
            report = {name: claim for name, claim in ALL_ENFORCED_WITH_EVIDENCE.items()
                      if name != missing}
            verdict, reason = autonomy_floor.floor_verdict("A0", report)
            self.assertEqual(verdict, autonomy_floor.REFUSE,
                             "expected refuse missing %s, got %s" % (missing, reason))
            self.assertIn(missing, reason)

    def test_advisory_instead_of_enforced_refuses(self):
        for target in execution_boundary.CAPABILITIES:
            report = dict(ALL_ENFORCED_WITH_EVIDENCE)
            report[target] = _claim(host_adapter_spec.ADVISORY)
            verdict, reason = autonomy_floor.floor_verdict("A0", report)
            self.assertEqual(verdict, autonomy_floor.REFUSE,
                             "expected refuse for advisory %s, got %s" % (target, reason))

    def test_unsupported_instead_of_enforced_refuses(self):
        report = dict(ALL_ENFORCED_WITH_EVIDENCE)
        report["network"] = _claim(host_adapter_spec.UNSUPPORTED)
        verdict, _reason = autonomy_floor.floor_verdict("A0", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_enforced_claim_without_evidence_is_downgraded_and_refuses(self):
        # This is the deciding property's own sentence made concrete: a
        # host cannot claim enforced without the evidence that proves it,
        # so this must refuse rather than run degraded.
        report = dict(ALL_ENFORCED_WITH_EVIDENCE)
        report["credential"] = _claim(host_adapter_spec.ENFORCED, evidence=None)
        verdict, reason = autonomy_floor.floor_verdict("A0", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertIn("credential", reason)

    def test_enforced_claim_with_blank_evidence_is_downgraded_and_refuses(self):
        report = dict(ALL_ENFORCED_WITH_EVIDENCE)
        report["process"] = _claim(host_adapter_spec.ENFORCED, evidence="   ")
        verdict, _reason = autonomy_floor.floor_verdict("A0", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_unrecognised_claim_value_refuses(self):
        report = dict(ALL_ENFORCED_WITH_EVIDENCE)
        report["filesystem"] = _claim("made-up-level")
        verdict, _reason = autonomy_floor.floor_verdict("A0", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)


class A1RequiresCredentialAndProcessOnlyTest(unittest.TestCase):
    def test_credential_and_process_enforced_runs_even_without_filesystem(self):
        report = {
            "credential": _claim(host_adapter_spec.ENFORCED, "measured"),
            "process": _claim(host_adapter_spec.ENFORCED, "measured"),
        }
        verdict, reason = autonomy_floor.floor_verdict("A1", report)
        self.assertEqual(verdict, autonomy_floor.RUN, reason)

    def test_filesystem_and_network_enforced_alone_is_not_enough(self):
        # A0's requirements are a superset of A1's; this proves A1 does
        # NOT silently require the other two as well.
        report = {
            "filesystem": _claim(host_adapter_spec.ENFORCED, "measured"),
            "network": _claim(host_adapter_spec.ENFORCED, "measured"),
        }
        verdict, _reason = autonomy_floor.floor_verdict("A1", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)

    def test_credential_enforced_process_advisory_refuses(self):
        report = {
            "credential": _claim(host_adapter_spec.ENFORCED, "measured"),
            "process": _claim(host_adapter_spec.ADVISORY),
        }
        verdict, reason = autonomy_floor.floor_verdict("A1", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)
        self.assertIn("process", reason)


class BadStateAGreenCheckWouldAlsoPassTest(unittest.TestCase):
    """Names the bad state a weaker check would let through, per the
    worker contract's rule 5."""

    def test_host_that_only_advises_never_runs_a0_as_if_it_enforced(self):
        # The bad state: a host that only DECIDES (advisory) is treated as
        # a host that ACTS (enforced), and A0 runs on the strength of a
        # label instead of a real guarantee. Every capability present,
        # every claim recognised, none of them proven: still refused.
        report = {name: _claim(host_adapter_spec.ADVISORY)
                  for name in execution_boundary.CAPABILITIES}
        verdict, _reason = autonomy_floor.floor_verdict("A0", report)
        self.assertEqual(verdict, autonomy_floor.REFUSE)


class MainCliTest(unittest.TestCase):
    def _run(self, argv, stdin_text=""):
        buf = io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        try:
            with redirect_stdout(buf):
                code = autonomy_floor.main(argv)
        finally:
            sys.stdin = old_stdin
        return code, buf.getvalue()

    def test_missing_level_is_nodata_exit_2(self):
        code, out = self._run([], stdin_text="{}")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_invalid_json_on_stdin_is_nodata_exit_2(self):
        code, out = self._run(["--level", "A0"], stdin_text="{not json")
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_a2_with_empty_stdin_runs_exit_0(self):
        code, out = self._run(["--level", "A2"], stdin_text="")
        self.assertEqual(code, 0)
        self.assertIn("verdict=RUN", out)

    def test_a0_all_enforced_via_stdin_runs_exit_0(self):
        code, out = self._run(
            ["--level", "A0"], stdin_text=json.dumps(ALL_ENFORCED_WITH_EVIDENCE))
        self.assertEqual(code, 0)
        self.assertIn("verdict=RUN", out)

    def test_a0_with_empty_report_refuses_exit_1(self):
        code, out = self._run(["--level", "A0"], stdin_text="{}")
        self.assertEqual(code, 1)
        self.assertIn("verdict=REFUSE", out)


class ReduceHelperTest(unittest.TestCase):
    """Direct coverage of the small translation step between
    host_adapter_spec's four lower case words and capability_precheck's
    two upper case ones, so a broken mapping fails here, not only through
    floor_verdict's end to end behaviour."""

    def test_enforced_with_evidence_maps_to_precheck_enforced(self):
        graded = host_adapter_spec.capability_levels(
            {"process": _claim(host_adapter_spec.ENFORCED, "measured")})
        reduced = autonomy_floor._reduce(["process"], graded)
        self.assertEqual(reduced, {"process": capability_precheck.ENFORCED})

    def test_advisory_maps_to_precheck_advisory(self):
        graded = host_adapter_spec.capability_levels(
            {"network": _claim(host_adapter_spec.ADVISORY)})
        reduced = autonomy_floor._reduce(["network"], graded)
        self.assertEqual(reduced, {"network": capability_precheck.ADVISORY})

    def test_unsupported_passes_through_as_its_own_word(self):
        graded = host_adapter_spec.capability_levels(
            {"filesystem": _claim(host_adapter_spec.UNSUPPORTED)})
        reduced = autonomy_floor._reduce(["filesystem"], graded)
        self.assertEqual(reduced, {"filesystem": host_adapter_spec.UNSUPPORTED})

    def test_capability_not_mentioned_is_left_out_entirely(self):
        graded = host_adapter_spec.capability_levels({})
        reduced = autonomy_floor._reduce(["credential"], graded)
        self.assertEqual(reduced, {})


if __name__ == "__main__":
    unittest.main()
