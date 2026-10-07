#!/usr/bin/env python3
"""classify() must keep calling a raw interpreter exception a CRASH, and its carve-outs must stay narrow.

WHY THIS ONE. probe_build.py is the adversarial half of the loop's gate: every build the grader
passes is then attacked, and classify() is the single function that turns an observed outcome
into the verdict. Exit 1 happens only when it says CRASH or WRONG-ACCEPT?, so if CRASH ever
stops being reachable, every hostile probe on the estate reads CLEAN and the adversary stage
keeps running, keeps costing money, and stops gating anything. Nothing downstream re-reads the
raw probe lines, so that failure is completely silent.

The carve-outs are the pressure point, and both were widened deliberately under measurement:
the module records 124 of 838 CRASH verdicts being an adversary calling a function with the
wrong arity, and 33 of 38 probes on one build being refusals delivered as a VALUE. Each widening
was right and each is one edit away from swallowing real defects. This test pins the LINE:

  an adversary that could not CALL the function is excused (arity, missing attribute, argparse);
  a hostile value that reached the body and raised is a CRASH, because handling it is the build's job;
  a refusal returned as None, False or a quarantine record is a refusal, not an accept;
  a genuine accept of a hostile input is NOT quietly reclassified as a refusal.

Run: python3 scripts/test_probe_build_guard.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))
import probe_build as P  # noqa: E402


class RawExceptionsStayCrashes(unittest.TestCase):
    def test_a_hostile_value_that_reaches_the_body_is_a_CRASH(self):
        cases = {
            "unhashable id": ("TypeError", "builtins", "unhashable type: 'list'"),
            "None arithmetic": ("TypeError", "builtins", "unsupported operand type(s) for +: 'NoneType' and 'int'"),
            "not subscriptable": ("TypeError", "builtins", "'int' object is not subscriptable"),
            "not iterable": ("TypeError", "builtins", "'NoneType' object is not iterable"),
            "missing key": ("KeyError", "builtins", "'window'"),
            "index": ("IndexError", "builtins", "list index out of range"),
            "attribute on a value": ("AttributeError", "builtins", "'NoneType' object has no attribute 'strip'"),
            "recursion": ("RecursionError", "builtins", "maximum recursion depth exceeded"),
            "bad utf8": ("UnicodeDecodeError", "builtins", "invalid start byte"),
            "absent file": ("FileNotFoundError", "builtins", "No such file or directory"),
            "bare assert": ("AssertionError", "builtins", ""),
            "divide by zero": ("ZeroDivisionError", "builtins", "division by zero"),
            "a bare SystemExit": ("SystemExit", "builtins", "1"),
        }
        for why, args in cases.items():
            with self.subTest(why=why):
                self.assertEqual(P.classify(*args), "CRASH", "a %s was excused" % why)

    def test_the_carve_outs_still_apply_to_what_they_were_written_for(self):
        # the adversary could not CALL it: that is the probe's defect, not the build's. Such a probe never reached
        # the build, so it proves nothing about it: NO-DATA, which counts toward neither CLEAN nor DIRTY (a probe
        # denied for the wrong reason proves nothing). REFUSED would have counted it as a clean refusal, a pass
        # the build never earned. The contract moved to NO-DATA in commit 1b489c385 and this test followed on
        # 2026-09-22 after failing against it.
        # 2026-09-29: only at the probe's own call site (at_call, set by the harness from the traceback); the
        # same text raised inside the build's body is the build's defect and reads CRASH.
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "machine_capacity() takes 0 positional arguments but 2 were given", at_call=True), "NO-DATA")
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "f() missing 1 required positional argument: 'path'", at_call=True), "NO-DATA")
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "f() got an unexpected keyword argument 'mode'", at_call=True), "NO-DATA")
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "f() missing 1 required positional argument: 'path'"), "CRASH")
        self.assertEqual(P.classify("AttributeError", "builtins",
                                    "module 'slicer' has no attribute 'slice_sauce'"), "NO-DATA")
        self.assertEqual(P.classify("SystemExit", "builtins", "2"), "REFUSED")   # argparse usage exit
        self.assertEqual(P.classify("ValueError", "builtins", "spec is not acceptable"), "REFUSED")
        self.assertEqual(P.classify("RETURNED", "", "42"), "RETURNED")

    def test_the_arity_carve_out_does_not_cover_a_value_the_body_mishandled(self):
        # narrow on purpose: it excuses a failure to CALL, never a failure to HANDLE
        self.assertEqual(P.classify("TypeError", "builtins",
                                    "'<' not supported between instances of 'NoneType' and 'int'"), "CRASH")
        self.assertEqual(P.classify("TypeError", "builtins", "object of type 'NoneType' has no len()"), "CRASH")
        self.assertEqual(P.classify("AttributeError", "builtins",
                                    "'dict' object has no attribute 'sort'"), "CRASH")


class RefusalsAndAcceptsAreNotConfused(unittest.TestCase):
    def test_a_refusal_delivered_as_a_value_is_recognised(self):
        for text in ("None", "False", "(None, QuarantineRecord(reason='bad id'))",
                     "(False, ('not admissible',), None)", "admissible=False"):
            with self.subTest(text=text):
                self.assertIsNotNone(P.REFUSAL_VALUE.search(text), "a returned refusal read as an accept: %r" % text)

    def test_an_empty_result_is_a_value_not_a_refusal(self):
        # Audited defect C (scripts/test_probe_is_a_control.py, contract changed in b3713e813 on 2026-09-24): reading
        # [] {} () '' as refusals classified a wrong accept as the build correctly refusing. This file still expected
        # the old reading until 2026-09-26, so one of the two suites was red whichever way the regex went.
        for text in ("[]", "{}", "()", "''"):
            with self.subTest(text=text):
                self.assertIsNone(P.REFUSAL_VALUE.search(text), "an empty result read as a refusal: %r" % text)

    def test_a_genuine_accept_is_not_reclassified_as_a_refusal(self):
        # this is the direction that hides a wrong ACCEPT of a hostile input
        for text in ("'admin'", "{'ok': True, 'id': 'x'}", "1234", "'../../etc/passwd'",
                     "PolicyDecision(allowed=True)", "['a', 'b']"):
            with self.subTest(text=text):
                self.assertIsNone(P.REFUSAL_VALUE.search(text), "an accept was excused as a refusal: %r" % text)


class ReturnedValueVerdict(unittest.TestCase):
    """verdict_of: a CLI probe's non zero exit code is a refusal; everything else keeps the 2026-09-22 narrowing."""
    def test_a_cli_probe_returning_an_exit_code_is_refused(self):
        for v in ("1", "2", " 3 "):
            self.assertEqual(P.verdict_of("cli_missing_file", "RETURNED", v, {"cli_missing_file"}), "REFUSED", v)
        self.assertEqual(P.verdict_of("main_argv_bytes", "RETURNED", "2", set()), "REFUSED")
    def test_exit_zero_from_a_cli_probe_the_spec_blocks_is_a_wrong_accept(self):
        self.assertEqual(P.verdict_of("cli_binary_file", "RETURNED", "0", {"cli_binary_file"}), "WRONG-ACCEPT?")
    def test_a_non_cli_probe_returning_two_is_still_judged_by_the_block_list(self):
        self.assertEqual(P.verdict_of("count_of_rows", "RETURNED", "2", {"count_of_rows"}), "WRONG-ACCEPT?")
        self.assertEqual(P.verdict_of("count_of_rows", "RETURNED", "2", set()), "RETURNED")
    def test_a_refusal_value_and_a_crash_are_unchanged(self):
        self.assertEqual(P.verdict_of("x", "RETURNED", "(None, 'refused')", {"x"}), "REFUSED")
        self.assertEqual(P.verdict_of("x", "CRASH", "TypeError", {"x"}), "CRASH")


if __name__ == "__main__":
    unittest.main()
