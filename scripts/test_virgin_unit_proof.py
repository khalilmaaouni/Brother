#!/usr/bin/env python3
"""test_virgin_unit_proof: drives scripts/virgin_unit_proof.py's own verdict
logic both ways, which is the part of it that can lie.

The gate's real work (building the public export, installing both plugins
into an isolated Codex home from it, running the installed engine against
a toy) is not simulated here: that is what running the script itself does,
and its own output is the evidence (see docs/codex/SMOKE-RUNBOOK.md and the
calibration run this change's own PR quotes). What is tested is the piece a
wrong answer would look right in: `decide()`, the pure function that turns
a parsed receipt plus the run's own log text into PASS or FAIL;
`check_candidates()`, the isolation guard that refuses to let the proof
leak a hub checkout, a development tree, or a real ~/.claude or ~/.codex
into the runtime candidate list it is supposed to prove unreachable;
`classify_bundle_alone()`, which reads leg 2's own expected NO-DATA shape
under this packaging design; and `find_installed_brother_run()`, which
locates the engine a real Codex install actually produced.

Fixture shapes below (receipt "evidence"/"unproven" entries) are copied
from scripts/receipt_door.py's own receipt_record(): "evidence" is every
unit in state "verified", "unproven" is every unit in state "no-data" or
"refused", each carrying its own "reason"."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import virgin_unit_proof as vup  # noqa: E402

# E100: this test creates no temp trees of its own beyond tempfile.mkdtemp
# calls tmp_sandbox already redirects; installed for the same hygiene every
# other test module in this tree uses.
try:
    import tmp_sandbox
    tmp_sandbox.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))


def _receipt(evidence=None, unproven=None):
    return {"evidence": evidence or [], "unproven": unproven or []}


class TheVerdictLogic(unittest.TestCase):
    """decide(): the piece this gate's own PASS/FAIL turns on."""

    def test_one_integrated_unit_and_no_refusal_passes(self):
        body = _receipt(evidence=[{"id": "U1", "state": "verified"}])
        status, message = vup.decide(body, log_text="")
        self.assertEqual(status, "PASS")
        self.assertIn("1", message)

    def test_zero_integrated_with_a_refusal_fails_naming_it(self):
        body = _receipt(unproven=[{"id": "U1", "state": "refused",
                                   "reason": "the scheduler never admitted it"}])
        status, message = vup.decide(body, log_text="")
        self.assertEqual(status, "FAIL")
        self.assertIn("U1", message)
        self.assertIn("the scheduler never admitted it", message)

    def test_the_engines_own_nodata_line_fails_quoting_it_verbatim(self):
        """The calibration case: today's main. A unit that was NEVER CLAIMED
        (loop_bridge.load_parts() failed before any admission happened)
        reads a laundered "no-data" reason in its own receipt row, never
        the NO-DATA sentence itself, because that sentence only ever
        reached the run's log (receipts_for reads the Work document and the
        claim store, neither of which loop_bridge's stdout/stderr ever
        touches). This is the exact shape the calibration run on today's
        main produced: unproven carries "no-data", not "refused", and the
        real cause is only in the log."""
        body = _receipt(unproven=[{"id": "U1", "state": "no-data",
                                   "reason": "the files this unit changed "
                                             "were not recorded"}])
        log_text = ("some earlier line\n"
                   "NO-DATA: %s. Looked, in order: /nowhere/tools. Set "
                   "BROTHER_RUNTIME_ROOT to a checkout to override.\n"
                   "some later line" % vup.NODATA_SIGNATURE)
        status, message = vup.decide(body, log_text)
        self.assertEqual(status, "FAIL")
        self.assertIn(vup.NODATA_SIGNATURE, message)

    def test_the_log_signature_outranks_a_units_own_refusal_reason(self):
        """The exact shape the real calibration run produced: the unit DOES
        carry a "refused" state (a different, true-but-laundered sentence),
        and the log ALSO carries the NO-DATA signature. The signature must
        win, because it names the root cause and the refusal reason does
        not; naming the wrong sentence here is the failure this test
        exists to catch."""
        body = _receipt(unproven=[{"id": "U1", "state": "refused",
                                   "reason": "it was never started this run, "
                                             "because a dependency, a full "
                                             "slot or the scheduler's own "
                                             "admission check held it back"}])
        log_text = "NO-DATA: %s. Looked, in order: /nowhere.\n" % vup.NODATA_SIGNATURE
        status, message = vup.decide(body, log_text)
        self.assertEqual(status, "FAIL")
        self.assertIn(vup.NODATA_SIGNATURE, message)

    def test_no_receipt_at_all_falls_back_to_the_log_signature(self):
        status, message = vup.decide(
            None, "NO-DATA: %s.\n" % vup.NODATA_SIGNATURE,
            receipt_problem="no receipt path was found")
        self.assertEqual(status, "FAIL")
        self.assertIn(vup.NODATA_SIGNATURE, message)

    def test_no_receipt_and_no_log_signature_names_the_problem(self):
        status, message = vup.decide(None, "", receipt_problem="disk full")
        self.assertEqual(status, "FAIL")
        self.assertIn("disk full", message)

    def test_malformed_receipt_fails_rather_than_crashing(self):
        status, message = vup.decide({"nonsense": True}, "")
        self.assertEqual(status, "FAIL")

    def test_zero_integrated_zero_unproven_still_fails(self):
        status, message = vup.decide(_receipt(), "")
        self.assertEqual(status, "FAIL")

    def test_a_refused_unit_alongside_an_integrated_one_is_not_a_pass(self):
        body = _receipt(evidence=[{"id": "U1", "state": "verified"}],
                        unproven=[{"id": "U2", "state": "refused",
                                  "reason": "declined"}])
        status, message = vup.decide(body, "")
        self.assertEqual(status, "FAIL")
        self.assertIn("U2", message)


class TheCandidateGuard(unittest.TestCase):
    """check_candidates(): the proof's own isolation assertion. A candidate
    list that resolves outside the throwaway directory it was built under
    must never be allowed to run the rest of the proof, because a proof
    that could leak a hub checkout, a development tree, a real ~/.claude,
    or a real ~/.codex proves nothing."""

    def test_a_clean_candidate_list_under_the_throwaway_passes(self):
        throwaway = "/tmp/x/leg-install"
        ok, why = vup.check_candidates([
            throwaway + "/codex-home/plugins/cache/brother/brothermode/3.4.4/tools",
            throwaway + "/codex-home/skills/brothermode/tools",
            throwaway + "/home/Documents/BrotherModeUp/tools"], throwaway)
        self.assertTrue(ok, why)

    def test_a_candidate_outside_the_throwaway_fails_naming_it(self):
        bad = "/Users/someone/brother-hub/products/brothermode/tools"
        ok, why = vup.check_candidates(
            ["/tmp/x/leg-install/codex-home/skills/brothermode/tools", bad],
            "/tmp/x/leg-install")
        self.assertFalse(ok)
        self.assertIn(bad, why)

    def test_a_real_hub_checkout_path_is_caught(self):
        bad = "/Users/someone/brother-hub/products/brothermode/tools"
        ok, why = vup.check_candidates([bad], "/tmp/x/leg-install")
        self.assertFalse(ok)
        self.assertIn("brother-hub", why)

    def test_the_throwaway_root_itself_is_not_a_false_positive(self):
        throwaway = "/tmp/x/leg-install"
        ok, why = vup.check_candidates([throwaway], throwaway)
        self.assertTrue(ok, why)

    def test_a_sibling_directory_sharing_only_a_string_prefix_still_fails(self):
        # "/tmp/x/leg-install-extra" is not UNDER "/tmp/x/leg-install": a
        # bare string prefix check (rather than a real path-under check)
        # would wrongly let this one through.
        ok, why = vup.check_candidates(
            ["/tmp/x/leg-install-extra/tools"], "/tmp/x/leg-install")
        self.assertFalse(ok)


class TheBundleAloneClassifier(unittest.TestCase):
    """classify_bundle_alone(): under this packaging shape the engine ships
    as a sibling plugin, so bundle alone finding nothing is the design
    working and must read NO-DATA, never FAIL; only a deviation (an
    unexpected integration, or a failure for an unrelated reason) is a real
    FAIL of the whole run."""

    def test_the_calibrated_nodata_shape_reads_as_no_data(self):
        verdict, why = vup.classify_bundle_alone(
            "FAIL", "the engine reported NO-DATA: %s" % vup.NODATA_SIGNATURE)
        self.assertEqual(verdict, "NO-DATA")
        self.assertIn("as designed", why)

    def test_an_unexpected_pass_is_a_real_fail(self):
        verdict, why = vup.classify_bundle_alone(
            "PASS", "1 unit(s) integrated, 0 refused")
        self.assertEqual(verdict, "FAIL")
        self.assertIn("leaked", why)

    def test_a_fail_for_an_unrelated_reason_is_still_a_fail(self):
        verdict, why = vup.classify_bundle_alone(
            "FAIL", "U1 was refused: declined")
        self.assertEqual(verdict, "FAIL")
        self.assertNotIn("as designed", why)


class TheInstalledEngineFinder(unittest.TestCase):
    """find_installed_brother_run(): the installed brother plugin's own
    runtime/brother_run.py under an isolated Codex home's plugin cache,
    picking the highest version correctly (not by a plain string sort,
    which gets a double-digit segment wrong)."""

    def test_finds_a_real_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = os.path.join(tmp, "plugins", "cache", "brother",
                                   "brother", "1.0.6", "runtime")
            os.makedirs(runtime)
            path = os.path.join(runtime, "brother_run.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# stub\n")
            self.assertEqual(vup.find_installed_brother_run(tmp), path)

    def test_nothing_installed_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(vup.find_installed_brother_run(tmp))

    def test_the_highest_version_wins_even_with_a_double_digit_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            for version in ("1.0.6", "1.0.10", "1.0.9"):
                runtime = os.path.join(tmp, "plugins", "cache", "brother",
                                       "brother", version, "runtime")
                os.makedirs(runtime)
                with open(os.path.join(runtime, "brother_run.py"), "w",
                          encoding="utf-8") as fh:
                    fh.write("# stub\n")
            found = vup.find_installed_brother_run(tmp)
            self.assertIn(os.sep + "1.0.10" + os.sep, found)


class TheRuntimeEnvVarReader(unittest.TestCase):
    """runtime_env_var_name(): read, never retyped, from the exported
    loop_bridge.py's own source."""

    def test_reads_the_constant_from_a_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "loop_bridge.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('RUNTIME_ENV_VAR = "SOME_OTHER_NAME"\n')
            self.assertEqual(vup.runtime_env_var_name(path), "SOME_OTHER_NAME")

    def test_absent_file_is_none_not_a_crash(self):
        self.assertIsNone(vup.runtime_env_var_name("/no/such/file.py"))

    def test_a_file_with_no_constant_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "loop_bridge.py")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# nothing here\n")
            self.assertIsNone(vup.runtime_env_var_name(path))


class TheReceiptPathReader(unittest.TestCase):
    """find_receipt_path(): the engine's own last line, or the receipt
    door's own path convention, both accepted."""

    def test_reads_the_engines_own_last_line(self):
        stdout = ("brother_run: exit 0: ...\n"
                 "brother_run: receipt: /run/dir/receipt/receipt.json\n")
        self.assertEqual(vup.find_receipt_path(stdout, "/run/dir"),
                         "/run/dir/receipt/receipt.json")

    def test_falls_back_to_the_receipt_door_convention(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt_dir = os.path.join(tmp, "receipt")
            os.makedirs(receipt_dir)
            path = os.path.join(receipt_dir, "receipt.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            self.assertEqual(vup.find_receipt_path("no receipt line here", tmp),
                             path)

    def test_neither_present_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(vup.find_receipt_path("nothing", tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
